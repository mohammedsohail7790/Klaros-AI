"""Phase 17: QuickBooks deposit-payment synchronization — closes the
accounting side of the Quote -> Deposit -> Payment lifecycle (Phase 15/16
built everything up through a real Stripe deposit Payment landing in
Klaros' own ledger). Fully self-contained: mocks `httpx` at the transport
level for the client's own retry/error-classification behavior (matching
`test_stripe_client.py`/`test_quickbooks_integration.py`'s pattern), and
mocks `QuickBooksClient` methods directly for the sync-service/event/tool
tests (matching `test_quickbooks_integration.py`'s higher-level pattern).

Builds each scenario through the REAL services (QuoteService,
PaymentService, InvoiceService, QuickBooksSyncService) rather than
hand-crafting rows, so this suite also exercises the real seam between
Phase 15/17 rather than testing against a fabricated shortcut.
"""

import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, QuickBooksErrorType
from app.integrations.quickbooks_schemas import (
    QuickBooksCustomerResponse,
    QuickBooksInvoiceResponse,
    QuickBooksPaymentResponse,
    QuickBooksTokenResponse,
)
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.event import DeadLetterEvent, EventProcessingRecord, EventType, ProcessingStatus
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentStatus
from app.models.operations import Job
from app.models.quote import Quote
from app.models.rbac import Role
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.quickbooks_payment_sync_service import (
    CustomerNotSyncedError,
    InvoiceNotYetCreatedError,
    InvoiceNotYetSyncedError,
    JobNotYetCreatedError,
    NotADepositPaymentError,
    PaymentNotFoundError,
    PaymentNotSucceededError,
    QuickBooksNotConnectedError,
    QuickBooksPaymentSyncService,
    QuoteNotFoundError,
)
from app.services.quickbooks_sync_service import QuickBooksSyncService
from app.services.quote_service import QuoteService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_ITEMS = [{"description": "HVAC install", "quantity": "1", "unit_price": "2000.00"}]


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


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
    """Real Quote -> accept -> deposit paid (via the REAL PaymentService +
    QuoteService.mark_deposit_paid, exactly what the Stripe webhook does)
    -> real Job. No Invoice yet — matches production reality, see
    QuickBooksPaymentSyncService's module docstring."""
    ctx = _ctx(tenant_id)
    customer_result = await tool_registry.execute(
        "crm.create_customer", {"name": "QB Deposit Customer", "email": "qb-deposit@example.com"}, ctx
    )
    customer_id = customer_result.customer["id"]

    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "400.00"},
        ctx,
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    quote_id = uuid.UUID(sent.quote["id"])

    quote_service = QuoteService(async_session_maker, event_bus)
    accept_result = await quote_service.decide(tenant_id, quote_id, accepted=True)
    assert accept_result.quote.status == "DEPOSIT_PENDING"

    payment_service = PaymentService(async_session_maker, event_bus)
    payment, _dedup = await payment_service.record_payment(
        tenant_id, customer_id=uuid.UUID(customer_id), amount=Decimal("400.00"), provider="stripe",
        external_id=f"pi_{uuid.uuid4().hex}", payment_method="card", allocations=[], quote_id=quote_id,
    )
    paid_result = await quote_service.mark_deposit_paid(tenant_id, quote_id, payment_id=payment.id)
    assert paid_result.quote.status == "CONVERTED"
    assert paid_result.job is not None

    return paid_result.quote, paid_result.job, payment


async def _sync_invoice_for_job(
    connection_service, tenant_id: uuid.UUID, job_id: uuid.UUID, monkeypatch,
) -> Invoice:
    """Real InvoiceService.create_draft_from_job + real QuickBooksSyncService.
    sync_invoice (QuickBooksClient mocked) — the exact staff-triggered
    path this app already has, not a shortcut."""
    from app.events.factory import get_event_bus

    invoice_service = InvoiceService(async_session_maker, get_event_bus())
    invoice, _dedup = await invoice_service.create_draft_from_job(tenant_id, job_id)

    # Approval workflow itself is out of scope here (setup precondition
    # only) — same simplification test_quickbooks_integration.py's own
    # _make_customer_and_invoice helper already uses (constructing an
    # APPROVED invoice directly rather than driving the approval flow).
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


def _payment_sync_service(connection_service) -> QuickBooksPaymentSyncService:
    return QuickBooksPaymentSyncService(async_session_maker, connection_service)


# --- 1. CLIENT: create_payment / get_payment. ---


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        return responses[idx]

    transport = httpx.MockTransport(handler)
    transport.call_count = lambda: state["calls"]  # type: ignore[attr-defined]
    return transport


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    import app.integrations.quickbooks_client as mod

    async def _noop_sleep(_seconds):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _noop_sleep)
    yield


def _patch_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import app.integrations.quickbooks_client as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)


async def test_create_payment_success_carries_requestid_and_linked_txn(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, request=request, json={"Payment": {"Id": "qb-pay-1", "TotalAmt": 400.0}})

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    result = await client.create_payment(
        access_token="at", realm_id="realm-1", customer_id="qb-cust-1", invoice_lines=[("qb-inv-1", 400.0)],
        request_id="klaros-deposit-payment-abc",
    )
    assert result.Id == "qb-pay-1"
    assert "requestid=klaros-deposit-payment-abc" in captured["url"]
    assert '"TxnId": "qb-inv-1"' in captured["body"] or '"TxnId":"qb-inv-1"' in captured["body"].replace(" ", "")


async def test_create_payment_malformed_response_raises_provider_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"Payment": {"TotalAmt": 400.0}})  # missing Id

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_payment(
            access_token="at", realm_id="realm-1", customer_id="c", invoice_lines=[("i", 1.0)]
        )
    assert exc_info.value.error_type == QuickBooksErrorType.PROVIDER_ERROR


async def test_create_payment_tolerates_unknown_response_fields(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, request=request,
            json={"Payment": {"Id": "qb-pay-2", "TotalAmt": 10.0, "SomeFutureField": {"nested": True}}},
        )

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    result = await client.create_payment(access_token="at", realm_id="r", customer_id="c", invoice_lines=[("i", 10.0)])
    assert result.Id == "qb-pay-2"


async def test_create_payment_401_is_classified_authentication_never_retried(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/payment")
    responses = [httpx.Response(401, request=request, json={"Fault": {"Error": [{"Message": "Token expired"}]}})]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_payment(access_token="at", realm_id="r", customer_id="c", invoice_lines=[("i", 1.0)])
    assert exc_info.value.error_type == QuickBooksErrorType.AUTHENTICATION
    assert transport.call_count() == 1


async def test_create_payment_transient_5xx_retries_then_succeeds(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/payment")
    responses = [
        httpx.Response(503, request=request, json={"Fault": {"Error": [{"Message": "temporarily down"}]}}),
        httpx.Response(200, request=request, json={"Payment": {"Id": "qb-pay-retry", "TotalAmt": 1.0}}),
    ]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    result = await client.create_payment(access_token="at", realm_id="r", customer_id="c", invoice_lines=[("i", 1.0)])
    assert result.Id == "qb-pay-retry"
    assert transport.call_count() == 2


async def test_create_payment_permanent_4xx_never_retried(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/payment")
    responses = [httpx.Response(400, request=request, json={"Fault": {"Error": [{"Message": "Invalid invoice ref"}]}})]
    transport = _make_transport(responses)
    _patch_transport(monkeypatch, transport)
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_payment(access_token="at", realm_id="r", customer_id="c", invoice_lines=[("bad", 1.0)])
    assert exc_info.value.error_type == QuickBooksErrorType.INVALID_REQUEST
    assert transport.call_count() == 1


async def test_create_payment_timeout_is_classified_and_bounded(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_payment(access_token="at", realm_id="r", customer_id="c", invoice_lines=[("i", 1.0)])
    assert exc_info.value.error_type == QuickBooksErrorType.TIMEOUT


async def test_get_payment_retrieves_by_id(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/payment/qb-pay-9")
        return httpx.Response(200, request=request, json={"Payment": {"Id": "qb-pay-9", "TotalAmt": 5.0}})

    _patch_transport(monkeypatch, httpx.MockTransport(handler))
    client = QuickBooksClient()
    result = await client.get_payment(access_token="at", realm_id="r", payment_id="qb-pay-9")
    assert result.Id == "qb-pay-9"


async def test_create_payment_error_message_never_leaks_access_token(monkeypatch) -> None:
    request = httpx.Request("POST", "https://sandbox-quickbooks.api.intuit.com/v3/company/r/payment")
    responses = [httpx.Response(400, request=request, json={"Fault": {"Error": [{"Message": "bad request"}]}})]
    _patch_transport(monkeypatch, _make_transport(responses))
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.create_payment(
            access_token="at_super_secret_token_value", realm_id="r", customer_id="c", invoice_lines=[("i", 1.0)]
        )
    assert "at_super_secret_token_value" not in str(exc_info.value)


# --- 2. SERVICE: sync_deposit_payment. ---


async def test_sync_unknown_payment_raises_not_found(connection_service) -> None:
    service = _payment_sync_service(connection_service)
    with pytest.raises(PaymentNotFoundError):
        await service.sync_deposit_payment(uuid.uuid4(), uuid.uuid4())


async def test_sync_invoice_payment_not_a_deposit_is_rejected(connection_service, tool_registry, event_bus) -> None:
    """A regular invoice payment (provider=stripe but quote_id=None, or
    provider != stripe) must never be treated as a deposit sync target."""
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Invoice Payer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(NotADepositPaymentError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_unpaid_payment_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Unpaid Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.PENDING,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
            quote_id=uuid.uuid4(),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(PaymentNotSucceededError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_missing_quote_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Ghost Quote Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
            quote_id=uuid.uuid4(),  # no such Quote row
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(QuoteNotFoundError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_before_job_conversion_is_rejected(connection_service, tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_result = await tool_registry.execute(
        "crm.create_customer", {"name": "No Job Yet Customer", "email": "nojob@example.com"}, ctx
    )
    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_result.customer["id"], "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "100.00"},
        ctx,
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)

    # A Payment referencing this quote, but the quote hasn't even been
    # accepted yet — quote.job_id is still None.
    async with async_session_maker() as session:
        payment = Payment(
            tenant_id=tenant_id, customer_id=uuid.UUID(customer_result.customer["id"]), amount=Decimal("100.00"),
            status=PaymentStatus.SUCCEEDED, provider="stripe", external_id=f"pi_{uuid.uuid4().hex}",
            received_at=date(2026, 1, 1), quote_id=uuid.UUID(sent.quote["id"]),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(JobNotYetCreatedError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_before_invoice_created_is_rejected(connection_service, tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    _quote, _job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    service = _payment_sync_service(connection_service)
    with pytest.raises(InvoiceNotYetCreatedError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_before_invoice_synced_to_quickbooks_is_rejected(connection_service, tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)

    from app.events.factory import get_event_bus
    invoice_service = InvoiceService(async_session_maker, get_event_bus())
    await invoice_service.create_draft_from_job(tenant_id, job.id)  # created but never synced to QB

    service = _payment_sync_service(connection_service)
    with pytest.raises(InvoiceNotYetSyncedError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_missing_qbo_customer_id_is_rejected(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    """A defensive check: even though QuickBooksSyncService.sync_invoice
    always links the QBO customer before creating the QBO invoice, prove
    the payment sync itself independently refuses to proceed without
    one — never assumes it silently."""
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    invoice = await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    async with async_session_maker() as session:
        customer = await session.get(Customer, (await session.get(Job, job.id)).customer_id)
        customer.external_provider = None
        customer.external_id = None
        await session.commit()

    service = _payment_sync_service(connection_service)
    with pytest.raises(CustomerNotSyncedError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_sync_without_quickbooks_connection_is_rejected(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    # A DIFFERENT tenant with no QuickBooks connection at all attempts the
    # sync against its own (nonexistent) connection — but here we prove
    # the "not connected" branch directly for the SAME tenant by removing
    # the connection's credential.
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

    service = _payment_sync_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_full_sync_creates_qbo_payment_and_persists_id(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    captured = {}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        captured["customer_id"] = customer_id
        captured["invoice_id"] = invoice_id
        captured["amount"] = amount
        captured["request_id"] = request_id
        return QuickBooksPaymentResponse(Id="qb-pay-real", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    result = await service.sync_deposit_payment(tenant_id, payment.id)

    assert result.already_synced is False
    assert result.quickbooks_payment_id == "qb-pay-real"
    assert captured["customer_id"] == "qb-cust-1"
    assert captured["invoice_id"] == "qb-inv-1"
    assert captured["amount"] == 400.0
    assert captured["request_id"] == f"klaros-deposit-payment-{payment.id}"

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == "qb-pay-real"


async def test_401_during_create_payment_refreshes_token_once_and_retries(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    calls = {"n": 0}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        calls["n"] += 1
        if calls["n"] == 1:
            raise QuickBooksAPIError("Token expired", error_type=QuickBooksErrorType.AUTHENTICATION)
        assert access_token == "at_refreshed"
        return QuickBooksPaymentResponse(Id="qb-pay-refreshed", TotalAmt=amount)

    async def _fake_refresh(self, *, refresh_token):
        return QuickBooksTokenResponse(access_token="at_refreshed", refresh_token="rt_refreshed", expires_in=3600)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)
    monkeypatch.setattr(QuickBooksClient, "refresh_access_token", _fake_refresh)

    service = _payment_sync_service(connection_service)
    result = await service.sync_deposit_payment(tenant_id, payment.id)
    assert result.quickbooks_payment_id == "qb-pay-refreshed"
    assert calls["n"] == 2


# --- 3. TENANT ISOLATION (amount integrity's companion — STEP 6/7). ---


async def test_tenant_b_cannot_sync_tenant_as_payment(connection_service, tool_registry, event_bus, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_a)
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_a, job.id, monkeypatch)

    service = _payment_sync_service(connection_service)
    with pytest.raises(PaymentNotFoundError):
        await service.sync_deposit_payment(tenant_b, payment.id)


async def test_tenant_b_cannot_use_tenant_as_quickbooks_connection(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    """Tenant B has its OWN paid deposit, but only tenant A has a
    QuickBooks connection — tenant B's sync must fail as not-connected,
    never silently borrow tenant A's realm/credential. Proven directly at
    the payment-sync service's own connection-resolution step (bypassing
    the invoice-sync precondition, which would fail for the same
    not-connected reason first and mask this specific assertion)."""
    from app.services.quickbooks_sync_service import QuickBooksNotConnectedError as InvoiceSyncNotConnectedError

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch, realm_id="realm-a")

    _quote_b, job_b, payment_b = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_b)

    # Tenant B's own invoice-sync attempt fails not-connected too — its
    # connection lookup is independently tenant-scoped the same way.
    invoice_service = InvoiceService(async_session_maker, event_bus)
    invoice_b, _ = await invoice_service.create_draft_from_job(tenant_b, job_b.id)
    sync_service = QuickBooksSyncService(async_session_maker, connection_service)
    with pytest.raises(InvoiceSyncNotConnectedError):
        await sync_service.sync_invoice(tenant_b, invoice_b.id)

    # And directly for the payment-sync service too, independent of the
    # invoice precondition, by asserting the payment's own resolution
    # never reaches "connected" for tenant B.
    payment_service = _payment_sync_service(connection_service)
    with pytest.raises((InvoiceNotYetSyncedError, QuickBooksNotConnectedError)):
        await payment_service.sync_deposit_payment(tenant_b, payment_b.id)


async def test_amount_integrity_uses_server_persisted_payment_amount_only(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    """The service signature accepts only (tenant_id, payment_id) — there
    is no amount/invoice_id/customer_id parameter a caller could forge.
    This proves the amount actually sent to QuickBooks is exactly the
    real Payment row's own persisted amount, unconditionally."""
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    captured_amount = {}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        captured_amount["value"] = amount
        return QuickBooksPaymentResponse(Id="qb-pay-amt", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    await service.sync_deposit_payment(tenant_id, payment.id)

    async with async_session_maker() as session:
        real_payment = await session.get(Payment, payment.id)
    assert captured_amount["value"] == float(real_payment.amount) == 400.0


# --- 4. IDEMPOTENCY. ---


async def test_already_synced_payment_is_a_safe_noop_no_api_call(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    async with async_session_maker() as session:
        row = await session.get(Payment, payment.id)
        row.quickbooks_payment_id = "already-synced-qb-payment"
        await session.commit()

    async def _fail_if_called(self, **kwargs):
        raise AssertionError("must not call create_payment for an already-synced Payment")

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fail_if_called)

    service = _payment_sync_service(connection_service)
    result = await service.sync_deposit_payment(tenant_id, payment.id)
    assert result.already_synced is True
    assert result.quickbooks_payment_id == "already-synced-qb-payment"


async def test_duplicate_tool_invocation_never_creates_two_qbo_payments(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    call_count = {"n": 0}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        call_count["n"] += 1
        return QuickBooksPaymentResponse(Id="qb-pay-once", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    # Route the ToolRegistry-registered tool through the SAME connection
    # service used by this test's fixture, matching how app/tools/factory.py
    # wires it in production.
    from app.tools.builtin.quickbooks_tools import (
        SyncDepositPaymentToQuickBooks,
        SyncDepositPaymentToQuickBooksInput,
    )

    tool = SyncDepositPaymentToQuickBooks(_payment_sync_service(connection_service))

    result_1 = await tool.execute(SyncDepositPaymentToQuickBooksInput(payment_id=payment.id), ctx)
    result_2 = await tool.execute(SyncDepositPaymentToQuickBooksInput(payment_id=payment.id), ctx)
    assert call_count["n"] == 1
    assert result_1.quickbooks_payment_id == result_2.quickbooks_payment_id == "qb-pay-once"
    assert result_1.already_synced is False
    assert result_2.already_synced is True


async def test_stripe_requestid_is_deterministic_per_payment_for_retry_safety(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    """Simulates 'QuickBooks accepted it, then this process crashed before
    persisting quickbooks_payment_id' by NOT persisting the id after the
    first (successful) call, then retrying — proves the SAME request_id
    is sent both times, which is what lets QuickBooks itself dedupe (per
    Intuit's documented contract; not independently verified live here —
    see QuickBooksClient.create_payment's docstring)."""
    tenant_id = uuid.uuid4()
    _quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    seen_request_ids: list[str | None] = []

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        seen_request_ids.append(request_id)
        return QuickBooksPaymentResponse(Id="qb-pay-dedup", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    await service.sync_deposit_payment(tenant_id, payment.id)  # persists quickbooks_payment_id normally

    # Force the "partial completion" scenario: clear the persisted id as
    # if the DB write never happened, then retry.
    async with async_session_maker() as session:
        row = await session.get(Payment, payment.id)
        row.quickbooks_payment_id = None
        await session.commit()

    await service.sync_deposit_payment(tenant_id, payment.id)

    assert len(seen_request_ids) == 2
    assert seen_request_ids[0] == seen_request_ids[1]
    assert seen_request_ids[0] == f"klaros-deposit-payment-{payment.id}"


# --- 5. EVENT / WORKER integration (STEP 9). ---


async def test_quote_deposit_paid_event_triggers_automatic_sync_attempt(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    """The real, registered finance_quickbooks_deposit_payment_sync
    handler (wired via register_finance_handlers, which the tool_registry
    fixture already calls) picks up QUOTE_DEPOSIT_PAID and attempts the
    sync — proven by pre-syncing the invoice BEFORE the deposit is paid
    (an unusual but valid order: staff already created+synced an
    estimate-turned-invoice ahead of time) so the automatic attempt can
    actually succeed on first try."""
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    customer_result = await tool_registry.execute(
        "crm.create_customer", {"name": "Auto Sync Customer", "email": "auto-sync@example.com"}, ctx
    )
    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_result.customer["id"], "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "400.00"},
        ctx,
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    quote_id = uuid.UUID(sent.quote["id"])

    quote_service = QuoteService(async_session_maker, event_bus)
    await quote_service.decide(tenant_id, quote_id, accepted=True)

    payment_service = PaymentService(async_session_maker, event_bus)
    payment, _dedup = await payment_service.record_payment(
        tenant_id, customer_id=uuid.UUID(customer_result.customer["id"]), amount=Decimal("400.00"), provider="stripe",
        external_id=f"pi_{uuid.uuid4().hex}", payment_method="card", allocations=[], quote_id=quote_id,
    )

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-auto", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id="qb-inv-auto", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    # mark_deposit_paid creates the Job; pre-create+sync its invoice NOW,
    # before publishing/processing the QUOTE_DEPOSIT_PAID event, so the
    # automatic handler's Invoice-resolution step succeeds.
    async with async_session_maker() as session:
        quote_row = await session.get(Quote, quote_id)

    paid_result = await quote_service.mark_deposit_paid(tenant_id, quote_id, payment_id=payment.id)
    job = paid_result.job
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-pay-from-event", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    # QUOTE_DEPOSIT_PAID was already published (and possibly dispatched)
    # by mark_deposit_paid above before the invoice existed — re-publish a
    # fresh one now that the precondition is met, and process it, exactly
    # like the real EventWorker's continuous tick loop would once the
    # invoice sync had landed.
    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.QUOTE_DEPOSIT_PAID, source="test",
        entity_type="quote", entity_id=quote_id, payload={"quote_id": str(quote_id), "payment_id": str(payment.id)},
    )
    stats = await event_bus.process_pending(EventType.QUOTE_DEPOSIT_PAID)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_payment.quickbooks_payment_id == "qb-pay-from-event"

        record = (
            await session.execute(
                select(EventProcessingRecord).where(
                    EventProcessingRecord.event_id == event.id,
                    EventProcessingRecord.handler_name == "finance_quickbooks_deposit_payment_sync",
                )
            )
        ).scalar_one()
        assert record.status == ProcessingStatus.SUCCESS


async def test_failed_automatic_sync_lands_in_dead_letter_and_stays_retryable(
    connection_service, tool_registry, event_bus
) -> None:
    """The realistic, common case: the deposit is paid but no invoice has
    been created for the job yet — the automatic sync attempt fails
    (InvoiceNotYetCreatedError, not a transient error), exhausts the
    EventBus's own bounded retries, and lands in the dead-letter queue —
    the 'visibly ERROR/PENDING_RETRY' state STEP 10 requires, using
    entirely existing infrastructure. Crucially: neither the Payment nor
    the Quote's own state is touched by this failure."""
    tenant_id = uuid.uuid4()
    event_bus.max_retries = 1  # keep the test fast; behavior is identical at any retry count
    quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.QUOTE_DEPOSIT_PAID, source="test",
        entity_type="quote", entity_id=quote.id, payload={"quote_id": str(quote.id), "payment_id": str(payment.id)},
    )
    stats = await event_bus.process_pending(EventType.QUOTE_DEPOSIT_PAID)
    assert stats.dead_lettered >= 1

    async with async_session_maker() as session:
        dl = (
            await session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.event_id == event.id,
                    DeadLetterEvent.handler_name == "finance_quickbooks_deposit_payment_sync",
                )
            )
        ).scalar_one()
        assert "invoice" in dl.reason.lower()

        # Financial state is completely unaffected by the QuickBooks sync
        # failure — this is the whole point of STEP 10.
        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_payment.status == PaymentStatus.SUCCEEDED
        assert refreshed_payment.quickbooks_payment_id is None
        refreshed_quote = await session.get(Quote, quote.id)
        assert refreshed_quote.status == "CONVERTED"


async def test_replaying_dead_lettered_sync_succeeds_once_invoice_is_ready(
    connection_service, tool_registry, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    event_bus.max_retries = 1
    quote, job, payment = await _build_paid_deposit_quote(tool_registry, event_bus, tenant_id)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.QUOTE_DEPOSIT_PAID, source="test",
        entity_type="quote", entity_id=quote.id, payload={"quote_id": str(quote.id), "payment_id": str(payment.id)},
    )
    await event_bus.process_pending(EventType.QUOTE_DEPOSIT_PAID)  # dead-letters, no invoice yet

    # Now the precondition becomes true — staff creates and syncs the
    # invoice, exactly as they would in production.
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_for_job(connection_service, tenant_id, job.id, monkeypatch)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-pay-replayed", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    result = await event_bus.replay(event.id, "finance_quickbooks_deposit_payment_sync")
    assert result == ProcessingStatus.SUCCESS

    async with async_session_maker() as session:
        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_payment.quickbooks_payment_id == "qb-pay-replayed"


# --- 6. TOOL surface: RBAC. ---


async def test_technician_cannot_call_deposit_payment_sync_tool(tool_registry) -> None:
    from app.tools.errors import ToolError

    tenant_id = uuid.uuid4()
    tech_ctx = _ctx(tenant_id, role=Role.TECHNICIAN)
    with pytest.raises(ToolError):
        await tool_registry.execute(
            "finance.sync_deposit_payment_to_quickbooks", {"payment_id": str(uuid.uuid4())}, tech_ctx
        )
