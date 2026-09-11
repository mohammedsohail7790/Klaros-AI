"""Phase 18: QuickBooks refund synchronization — closes the loop from a
real, completed Klaros Refund (Phase 12C/12F: real Stripe refund + real
bookkeeping) into a tenant's connected QuickBooks Online company as a
RefundReceipt applied against the original Payment it reverses (Phase 17
built that Payment's own QuickBooks sync). Fully self-contained: mocks
`httpx` at the transport level for the client's own retry/error-
classification behavior, and mocks `QuickBooksClient`/`StripeClient`
methods directly for the sync-service/event/tool tests, matching
`test_quickbooks_deposit_payment_sync.py`'s established pattern.

Every scenario is built through the REAL services (QuoteService,
PaymentService, InvoiceService, QuickBooksSyncService,
QuickBooksPaymentSyncService) rather than hand-crafted rows.
"""

import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.integrations.quickbooks_schemas import (
    QuickBooksCustomerResponse,
    QuickBooksInvoiceResponse,
    QuickBooksPaymentResponse,
    QuickBooksRefundReceiptResponse,
    QuickBooksTokenResponse,
)
from app.integrations.stripe_client import StripeClient
from app.integrations.stripe_schemas import StripeRefundResponse
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.event import DeadLetterEvent, EventProcessingRecord, EventType, ProcessingStatus
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentStatus, Refund, RefundStatus
from app.models.operations import Job
from app.models.quote import Quote
from app.models.rbac import Role
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.quickbooks_payment_sync_service import QuickBooksPaymentSyncService
from app.services.quickbooks_refund_sync_service import (
    CustomerNotSyncedError,
    PaymentNotFoundError,
    PaymentNotSyncedError,
    QuickBooksNotConnectedError,
    QuickBooksRefundSyncService,
    RefundNotCompletedError,
    RefundNotFoundError,
)
from app.services.quickbooks_sync_service import QuickBooksSyncService
from app.services.quote_service import QuoteService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_ITEMS = [{"description": "Roof replacement", "quantity": "1", "unit_price": "1500.00"}]


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


@pytest.fixture(autouse=True)
def _configure_stripe_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    yield


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    import app.integrations.quickbooks_client as mod

    async def _noop_sleep(_seconds):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _noop_sleep)
    yield


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch, realm_id: str = "realm-1") -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo

    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Acme Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": realm_id},
        created_by=None, external_account_id=realm_id, scopes="com.intuit.quickbooks.accounting",
    )


async def _build_paid_deposit_quote(tool_registry, event_bus: EventBus, tenant_id: uuid.UUID) -> tuple[Quote, Job, Payment]:
    ctx = _ctx(tenant_id)
    customer_result = await tool_registry.execute(
        "crm.create_customer", {"name": "QB Refund Customer", "email": "qb-refund@example.com"}, ctx
    )
    customer_id = customer_result.customer["id"]

    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "300.00"},
        ctx,
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    quote_id = uuid.UUID(sent.quote["id"])

    quote_service = QuoteService(async_session_maker, event_bus)
    accept_result = await quote_service.decide(tenant_id, quote_id, accepted=True)
    assert accept_result.quote.status == "DEPOSIT_PENDING"

    payment_service = PaymentService(async_session_maker, event_bus)
    payment, _dedup = await payment_service.record_payment(
        tenant_id, customer_id=uuid.UUID(customer_id), amount=Decimal("300.00"), provider="stripe",
        external_id=f"pi_{uuid.uuid4().hex}", payment_method="card", allocations=[], quote_id=quote_id,
    )
    paid_result = await quote_service.mark_deposit_paid(tenant_id, quote_id, payment_id=payment.id)
    assert paid_result.quote.status == "CONVERTED"
    assert paid_result.job is not None

    return paid_result.quote, paid_result.job, payment


async def _sync_invoice_for_job(connection_service, tenant_id: uuid.UUID, job_id: uuid.UUID, monkeypatch) -> Invoice:
    from app.events.factory import get_event_bus

    invoice_service = InvoiceService(async_session_maker, get_event_bus())
    invoice, _dedup = await invoice_service.create_draft_from_job(tenant_id, job_id)

    async with async_session_maker() as session:
        row = await session.get(Invoice, invoice.id)
        row.status = InvoiceStatus.APPROVED
        await session.commit()

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-1", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id="qb-inv-1", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    sync_service = QuickBooksSyncService(async_session_maker, connection_service)
    await sync_service.sync_invoice(tenant_id, invoice.id)

    async with async_session_maker() as session:
        return await session.get(Invoice, invoice.id)


async def _sync_payment_to_quickbooks(connection_service, tenant_id: uuid.UUID, payment_id: uuid.UUID, monkeypatch) -> Payment:
    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-pay-1", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)
    payment_sync_service = QuickBooksPaymentSyncService(async_session_maker, connection_service)
    await payment_sync_service.sync_deposit_payment(tenant_id, payment_id)

    async with async_session_maker() as session:
        return await session.get(Payment, payment_id)


async def _build_fully_synced_completed_refund(
    tool_registry, event_bus: EventBus, connection_service, tenant_id: uuid.UUID, monkeypatch,
    *, refund_amount: Decimal = Decimal("300.00"),
) -> tuple[Refund, Payment]:
    """The full precondition chain a real refund sync needs: paid deposit
    -> job -> approved+synced invoice -> synced payment -> a real,
    COMPLETED refund (via PaymentService.request_refund + decide_refund,
    with the real Stripe refund call mocked, not skipped)."""
    quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)
    payment = await _sync_payment_to_quickbooks(connection_service, tenant_id, payment.id, monkeypatch)

    async def _fake_stripe_create_refund(self, *, payment_intent_id, amount=None, reason=None, idempotency_key=None):
        return StripeRefundResponse(id=f"re_{uuid.uuid4().hex}", status="succeeded", amount=int((amount or 0) * 100), currency="usd")

    monkeypatch.setattr(StripeClient, "create_refund", _fake_stripe_create_refund)

    payment_service = PaymentService(async_session_maker, event_bus)
    refund = await payment_service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=refund_amount,
        reason="Customer requested", requested_by=None,
    )
    refund = await payment_service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)
    assert refund.status == "COMPLETED"
    return refund, payment


def _refund_sync_service(connection_service) -> QuickBooksRefundSyncService:
    return QuickBooksRefundSyncService(async_session_maker, connection_service)


# --- 1. CLIENT: create_refund_receipt / get_refund_receipt. ---


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        return responses[idx]

    transport = httpx.MockTransport(handler)
    transport.call_count = lambda: state["calls"]  # type: ignore[attr-defined]
    return transport


def _patch_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import app.integrations.quickbooks_client as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)


async def test_create_refund_receipt_success_carries_requestid_and_linked_payment(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, request=request, json={"RefundReceipt": {"Id": "qb-refund-1", "TotalAmt": 300.0}})

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    result = await client.create_refund_receipt(
        access_token="at", realm_id="realm-1", customer_id="qb-cust-1", payment_id="qb-pay-1",
        amount=300.0, request_id="klaros-refund-abc",
    )
    assert result.Id == "qb-refund-1"
    assert "requestid=klaros-refund-abc" in captured["url"]
    assert "qb-pay-1" in captured["body"]
    assert "Payment" in captured["body"]


async def test_create_refund_receipt_malformed_response_raises_provider_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"RefundReceipt": {"TotalAmt": 300.0}})  # missing Id

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=1.0)
    assert exc_info.value.error_type == QuickBooksErrorType.PROVIDER_ERROR


async def test_create_refund_receipt_tolerates_unknown_response_fields(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, request=request,
            json={"RefundReceipt": {"Id": "qb-refund-2", "TotalAmt": 5.0, "SomeFutureField": {"nested": True}}},
        )

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    result = await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=5.0)
    assert result.Id == "qb-refund-2"


async def test_create_refund_receipt_401_classified_authentication_never_retried(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/refundreceipt")
    responses = [httpx.Response(401, request=request, json={"Fault": {"Error": [{"Message": "Token expired"}]}})]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=1.0)
    assert exc_info.value.error_type == QuickBooksErrorType.AUTHENTICATION
    assert transport.call_count() == 1


async def test_create_refund_receipt_403_classified_authentication(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/refundreceipt")
    responses = [httpx.Response(403, request=request, json={"Fault": {"Error": [{"Message": "Forbidden"}]}})]
    _patch_transport(monkeypatch, _make_transport(responses))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=1.0)
    assert exc_info.value.error_type == QuickBooksErrorType.AUTHENTICATION


async def test_create_refund_receipt_transient_5xx_retries_then_succeeds(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/refundreceipt")
    responses = [
        httpx.Response(503, request=request, json={"Fault": {"Error": [{"Message": "temporarily down"}]}}),
        httpx.Response(200, request=request, json={"RefundReceipt": {"Id": "qb-refund-retry", "TotalAmt": 1.0}}),
    ]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    result = await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=1.0)
    assert result.Id == "qb-refund-retry"
    assert transport.call_count() == 2


async def test_create_refund_receipt_429_retries(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/refundreceipt")
    responses = [
        httpx.Response(429, request=request, json={"Fault": {"Error": [{"Message": "rate limited"}]}}),
        httpx.Response(200, request=request, json={"RefundReceipt": {"Id": "qb-refund-rl", "TotalAmt": 1.0}}),
    ]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    result = await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=1.0)
    assert result.Id == "qb-refund-rl"
    assert transport.call_count() == 2


async def test_create_refund_receipt_permanent_4xx_never_retried(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/refundreceipt")
    responses = [httpx.Response(400, request=request, json={"Fault": {"Error": [{"Message": "Invalid payment ref"}]}})]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="bad", amount=1.0)
    assert exc_info.value.error_type == QuickBooksErrorType.INVALID_REQUEST
    assert transport.call_count() == 1


async def test_create_refund_receipt_timeout_is_classified(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_refund_receipt(access_token="at", realm_id="r", customer_id="c", payment_id="p", amount=1.0)
    assert exc_info.value.error_type == QuickBooksErrorType.TIMEOUT


async def test_get_refund_receipt_retrieves_by_id(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/refundreceipt/qb-refund-9")
        return httpx.Response(200, request=request, json={"RefundReceipt": {"Id": "qb-refund-9", "TotalAmt": 5.0}})

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    result = await client.get_refund_receipt(access_token="at", realm_id="r", refund_receipt_id="qb-refund-9")
    assert result.Id == "qb-refund-9"


async def test_create_refund_receipt_error_never_leaks_access_token(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/refundreceipt")
    responses = [httpx.Response(400, request=request, json={"Fault": {"Error": [{"Message": "bad request"}]}})]
    _patch_transport(monkeypatch, _make_transport(responses))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_refund_receipt(
            access_token="at_super_secret_refund_token", realm_id="r", customer_id="c", payment_id="p", amount=1.0
        )
    assert "at_super_secret_refund_token" not in str(exc_info.value)


# --- 2. SERVICE: sync_refund_to_quickbooks. ---


async def test_sync_unknown_refund_raises_not_found(connection_service) -> None:
    service = _refund_sync_service(connection_service)
    with pytest.raises(RefundNotFoundError):
        await service.sync_refund_to_quickbooks(uuid.uuid4(), uuid.uuid4())


async def test_sync_requested_refund_is_rejected(connection_service) -> None:
    """A REQUESTED (not yet decided) refund must never sync — no money
    has actually moved back to the customer yet."""
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Pending Refund Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
            quickbooks_payment_id="qb-pay-x",
        )
        session.add(payment)
        await session.flush()
        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, amount=Decimal("50.00"), reason="test",
            status=RefundStatus.REQUESTED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)

    service = _refund_sync_service(connection_service)
    with pytest.raises(RefundNotCompletedError):
        await service.sync_refund_to_quickbooks(tenant_id, refund.id)


async def test_sync_rejected_refund_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Rejected Refund Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
            quickbooks_payment_id="qb-pay-x",
        )
        session.add(payment)
        await session.flush()
        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, amount=Decimal("50.00"), reason="test",
            status=RefundStatus.REJECTED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)

    service = _refund_sync_service(connection_service)
    with pytest.raises(RefundNotCompletedError):
        await service.sync_refund_to_quickbooks(tenant_id, refund.id)


async def test_sync_missing_payment_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        refund = Refund(
            tenant_id=tenant_id, payment_id=uuid.uuid4(), amount=Decimal("50.00"), reason="test",
            status=RefundStatus.COMPLETED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)

    service = _refund_sync_service(connection_service)
    with pytest.raises(PaymentNotFoundError):
        await service.sync_refund_to_quickbooks(tenant_id, refund.id)


async def test_sync_before_payment_synced_to_quickbooks_is_rejected(connection_service) -> None:
    """The genuine precondition: the ORIGINAL payment must already be a
    real QBO Payment before a refund can reference it."""
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Unsynced Payment Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
            # quickbooks_payment_id deliberately left unset
        )
        session.add(payment)
        await session.flush()
        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, amount=Decimal("50.00"), reason="test",
            status=RefundStatus.COMPLETED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)

    service = _refund_sync_service(connection_service)
    with pytest.raises(PaymentNotSyncedError):
        await service.sync_refund_to_quickbooks(tenant_id, refund.id)


async def test_sync_missing_qbo_customer_is_rejected(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    refund, payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    async with async_session_maker() as session:
        customer = await session.get(Customer, payment.customer_id)
        customer.external_provider = None
        customer.external_id = None
        await session.commit()

    service = _refund_sync_service(connection_service)
    with pytest.raises(CustomerNotSyncedError):
        await service.sync_refund_to_quickbooks(tenant_id, refund.id)


async def test_sync_without_quickbooks_connection_is_rejected(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    from app.models.integration import IntegrationConnection
    async with async_session_maker() as session:
        conn = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.provider == "quickbooks"
                )
            )
        ).scalar_one()
        conn.encrypted_credential = None
        await session.commit()

    service = _refund_sync_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.sync_refund_to_quickbooks(tenant_id, refund.id)


async def test_full_sync_creates_qbo_refund_receipt_and_persists_id(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    refund, payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    captured = {}

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        captured["customer_id"] = customer_id
        captured["payment_id"] = payment_id
        captured["amount"] = amount
        captured["request_id"] = request_id
        return QuickBooksRefundReceiptResponse(Id="qb-refund-real", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    service = _refund_sync_service(connection_service)
    result = await service.sync_refund_to_quickbooks(tenant_id, refund.id)

    assert result.already_synced is False
    assert result.quickbooks_refund_receipt_id == "qb-refund-real"
    assert captured["customer_id"] == "qb-cust-1"
    assert captured["payment_id"] == "qb-pay-1"
    assert captured["amount"] == 300.0
    assert captured["request_id"] == f"klaros-refund-{refund.id}"

    async with async_session_maker() as session:
        refreshed = await session.get(Refund, refund.id)
        assert refreshed.quickbooks_refund_receipt_id == "qb-refund-real"


async def test_partial_refund_amount_integrity(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    """A PARTIAL refund (< the full payment amount) must sync with its
    OWN amount, never the full payment amount."""
    tenant_id = uuid.uuid4()
    refund, payment = await _build_fully_synced_completed_refund(
        tool_registry, event_bus, connection_service, tenant_id, monkeypatch, refund_amount=Decimal("125.00")
    )
    assert payment.amount == Decimal("300.00")
    assert refund.amount == Decimal("125.00")

    captured_amount = {}

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        captured_amount["value"] = amount
        return QuickBooksRefundReceiptResponse(Id="qb-refund-partial", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    service = _refund_sync_service(connection_service)
    await service.sync_refund_to_quickbooks(tenant_id, refund.id)
    assert captured_amount["value"] == 125.0


async def test_401_during_create_refund_receipt_refreshes_token_once_and_retries(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    calls = {"n": 0}

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise QuickBooksAPIError("Token expired", error_type=QuickBooksErrorType.AUTHENTICATION)
        assert access_token == "at_refreshed"
        return QuickBooksRefundReceiptResponse(Id="qb-refund-refreshed", TotalAmt=amount)

    async def _fake_refresh(self, *, refresh_token):
        return QuickBooksTokenResponse(access_token="at_refreshed", refresh_token="rt_refreshed", expires_in=3600)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)
    monkeypatch.setattr(QuickBooksClient, "refresh_access_token", _fake_refresh)

    service = _refund_sync_service(connection_service)
    result = await service.sync_refund_to_quickbooks(tenant_id, refund.id)
    assert result.quickbooks_refund_receipt_id == "qb-refund-refreshed"
    assert calls["n"] == 2


# --- 3. TENANT ISOLATION. ---


async def test_tenant_b_cannot_sync_tenant_as_refund(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_a, monkeypatch)

    service = _refund_sync_service(connection_service)
    with pytest.raises(RefundNotFoundError):
        await service.sync_refund_to_quickbooks(tenant_b, refund.id)


async def test_tenant_b_cannot_use_tenant_as_quickbooks_connection_for_refund(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch, realm_id="realm-a")

    # Tenant B builds its OWN fully-synced refund against ITS OWN
    # (separately connected) QuickBooks realm, then we strip just that
    # connection's credential to prove tenant B can never fall back to
    # tenant A's real connection.
    refund_b, _payment_b = await _build_fully_synced_completed_refund(
        tool_registry, event_bus, connection_service, tenant_b, monkeypatch
    )
    from app.models.integration import IntegrationConnection
    async with async_session_maker() as session:
        conn_b = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.tenant_id == tenant_b, IntegrationConnection.provider == "quickbooks"
                )
            )
        ).scalar_one()
        conn_b.encrypted_credential = None
        await session.commit()

    service = _refund_sync_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.sync_refund_to_quickbooks(tenant_b, refund_b.id)


# --- 4. IDEMPOTENCY. ---


async def test_already_synced_refund_is_a_safe_noop_no_api_call(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    async with async_session_maker() as session:
        row = await session.get(Refund, refund.id)
        row.quickbooks_refund_receipt_id = "already-synced-qb-refund"
        await session.commit()

    async def _fail_if_called(self, **kwargs):
        raise AssertionError("must not call create_refund_receipt for an already-synced Refund")

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fail_if_called)

    service = _refund_sync_service(connection_service)
    result = await service.sync_refund_to_quickbooks(tenant_id, refund.id)
    assert result.already_synced is True
    assert result.quickbooks_refund_receipt_id == "already-synced-qb-refund"


async def test_duplicate_tool_invocation_never_creates_two_qbo_refund_receipts(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    call_count = {"n": 0}

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        call_count["n"] += 1
        return QuickBooksRefundReceiptResponse(Id="qb-refund-once", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    from app.tools.builtin.quickbooks_tools import SyncRefundToQuickBooks, SyncRefundToQuickBooksInput
    tool = SyncRefundToQuickBooks(_refund_sync_service(connection_service))

    result_1 = await tool.execute(SyncRefundToQuickBooksInput(refund_id=refund.id), ctx)
    result_2 = await tool.execute(SyncRefundToQuickBooksInput(refund_id=refund.id), ctx)
    assert call_count["n"] == 1
    assert result_1.quickbooks_refund_receipt_id == result_2.quickbooks_refund_receipt_id == "qb-refund-once"
    assert result_1.already_synced is False
    assert result_2.already_synced is True


async def test_requestid_is_deterministic_across_a_simulated_partial_completion_retry(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    seen_request_ids: list[str | None] = []

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        seen_request_ids.append(request_id)
        return QuickBooksRefundReceiptResponse(Id="qb-refund-dedup", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    service = _refund_sync_service(connection_service)
    await service.sync_refund_to_quickbooks(tenant_id, refund.id)

    async with async_session_maker() as session:
        row = await session.get(Refund, refund.id)
        row.quickbooks_refund_receipt_id = None  # simulate "crashed before persisting"
        await session.commit()

    await service.sync_refund_to_quickbooks(tenant_id, refund.id)

    assert len(seen_request_ids) == 2
    assert seen_request_ids[0] == seen_request_ids[1] == f"klaros-refund-{refund.id}"


# --- 5. EVENT / WORKER integration. ---


async def test_payment_refunded_event_triggers_automatic_sync_attempt(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    """The real, registered finance_quickbooks_refund_sync handler
    (wired via register_finance_handlers, which the tool_registry fixture
    already calls) picks up the EXISTING EventType.PAYMENT_REFUNDED — no
    new EventType needed — and syncs when the precondition is met."""
    tenant_id = uuid.uuid4()
    refund, _payment = await _build_fully_synced_completed_refund(tool_registry, event_bus, connection_service, tenant_id, monkeypatch)

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        return QuickBooksRefundReceiptResponse(Id="qb-refund-from-event", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    # decide_refund already published PAYMENT_REFUNDED once inside
    # _build_fully_synced_completed_refund — publish + process a fresh
    # one directly here so this test controls dispatch timing explicitly,
    # matching the Phase 17 event test's own pattern.
    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_REFUNDED, source="test",
        entity_type="refund", entity_id=refund.id, payload={"refund_id": str(refund.id), "approved": True},
    )
    stats = await event_bus.process_pending(EventType.PAYMENT_REFUNDED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        refreshed_refund = await session.get(Refund, refund.id)
        assert refreshed_refund.quickbooks_refund_receipt_id == "qb-refund-from-event"

        record = (
            await session.execute(
                select(EventProcessingRecord).where(
                    EventProcessingRecord.event_id == event.id,
                    EventProcessingRecord.handler_name == "finance_quickbooks_refund_sync",
                )
            )
        ).scalar_one()
        assert record.status == ProcessingStatus.SUCCESS


async def test_failed_automatic_refund_sync_lands_in_dead_letter_and_stays_retryable(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    """The realistic common case: the refund is completed but the
    original payment was never synced to QuickBooks (Phase 17's own
    scope boundary — only deposit payments have a sync path today) — the
    automatic sync attempt fails (PaymentNotSyncedError, not transient),
    exhausts the EventBus's bounded retries, and dead-letters. Neither
    the Refund nor the Payment's own state is touched by this failure."""
    tenant_id = uuid.uuid4()
    event_bus.max_retries = 1
    quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    # Deliberately skip syncing the payment to QuickBooks.

    async def _fake_stripe_create_refund(self, *, payment_intent_id, amount=None, reason=None, idempotency_key=None):
        return StripeRefundResponse(id=f"re_{uuid.uuid4().hex}", status="succeeded", amount=int((amount or 0) * 100), currency="usd")

    monkeypatch.setattr(StripeClient, "create_refund", _fake_stripe_create_refund)

    payment_service = PaymentService(async_session_maker, event_bus)
    refund = await payment_service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("300.00"),
        reason="Customer requested", requested_by=None,
    )
    refund = await payment_service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)
    assert refund.status == "COMPLETED"

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_REFUNDED, source="test",
        entity_type="refund", entity_id=refund.id, payload={"refund_id": str(refund.id), "approved": True},
    )
    stats = await event_bus.process_pending(EventType.PAYMENT_REFUNDED)
    assert stats.dead_lettered >= 1

    async with async_session_maker() as session:
        dl = (
            await session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.event_id == event.id,
                    DeadLetterEvent.handler_name == "finance_quickbooks_refund_sync",
                )
            )
        ).scalar_one()
        assert "quickbooks" in dl.reason.lower() or "synced" in dl.reason.lower()

        refreshed_refund = await session.get(Refund, refund.id)
        assert refreshed_refund.status == "COMPLETED"
        assert refreshed_refund.quickbooks_refund_receipt_id is None
        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_payment.status in ("REFUNDED", "PARTIALLY_REFUNDED")


async def test_replaying_dead_lettered_refund_sync_succeeds_once_payment_is_synced(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    event_bus.max_retries = 1
    quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)

    async def _fake_stripe_create_refund(self, *, payment_intent_id, amount=None, reason=None, idempotency_key=None):
        return StripeRefundResponse(id=f"re_{uuid.uuid4().hex}", status="succeeded", amount=int((amount or 0) * 100), currency="usd")

    monkeypatch.setattr(StripeClient, "create_refund", _fake_stripe_create_refund)

    payment_service = PaymentService(async_session_maker, event_bus)
    refund = await payment_service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("300.00"),
        reason="Customer requested", requested_by=None,
    )
    refund = await payment_service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_REFUNDED, source="test",
        entity_type="refund", entity_id=refund.id, payload={"refund_id": str(refund.id), "approved": True},
    )
    await event_bus.process_pending(EventType.PAYMENT_REFUNDED)  # dead-letters, payment not synced yet

    # Now the precondition becomes true.
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)
    await _sync_payment_to_quickbooks(connection_service, tenant_id, payment.id, monkeypatch)

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        return QuickBooksRefundReceiptResponse(Id="qb-refund-replayed", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    result = await event_bus.replay(event.id, "finance_quickbooks_refund_sync")
    assert result == ProcessingStatus.SUCCESS

    async with async_session_maker() as session:
        refreshed = await session.get(Refund, refund.id)
        assert refreshed.quickbooks_refund_receipt_id == "qb-refund-replayed"


# --- 6. TOOL surface: RBAC. ---


async def test_technician_cannot_call_refund_sync_tool(tool_registry) -> None:
    from app.tools.errors import ToolError

    tenant_id = uuid.uuid4()
    tech_ctx = _ctx(tenant_id, role=Role.TECHNICIAN)
    with pytest.raises(ToolError):
        await tool_registry.execute(
            "finance.sync_refund_to_quickbooks", {"refund_id": str(uuid.uuid4())}, tech_ctx
        )
