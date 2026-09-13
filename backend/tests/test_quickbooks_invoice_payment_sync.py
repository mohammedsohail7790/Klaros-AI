"""Phase 19: QuickBooks synchronization for ORDINARY invoice payments —
the counterpart to Phase 17's quote-deposit payment sync. A Stripe
payment allocated to an Invoice (`Payment.quote_id is None`, resolved via
`PaymentAllocation`) can now be pushed to QuickBooks as a Payment applied
against that invoice, exactly like a deposit payment but resolved through
a different (simpler) path — no Quote/Job involved.

Also proves the Phase 18 refund-sync service needed ZERO changes to work
with an ordinary-invoice-payment-originated `quickbooks_payment_id` — the
full internal chain Payment -> QuickBooks Payment -> Refund -> QuickBooks
RefundReceipt is exercised end to end.

Fully self-contained: mocks `httpx` at the transport level for the
client's own behavior (already covered by test_quickbooks_deposit_payment_
sync.py and test_quickbooks_refund_sync.py — not re-duplicated here) and
`QuickBooksClient`/`StripeClient` methods directly for the service/event/
tool tests, matching the established pattern from both prior phases.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
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
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentAllocation, PaymentStatus, Refund
from app.models.rbac import Role
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.payment_service import AllocationInput, PaymentService
from app.services.quickbooks_payment_sync_service import (
    AllocationSpansMultipleCustomersError,
    CustomerNotSyncedError,
    InvoiceNotYetCreatedError,
    InvoiceNotYetSyncedError,
    NoInvoiceAssociatedError,
    NotADepositPaymentError,
    NotAnInvoicePaymentError,
    NotAStripePaymentError,
    PaymentNotFoundError,
    PaymentNotSucceededError,
    QuickBooksNotConnectedError,
    QuickBooksPaymentSyncService,
)
from app.services.quickbooks_refund_sync_service import QuickBooksRefundSyncService
from app.services.quickbooks_sync_service import QuickBooksSyncService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


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


async def _make_customer_and_invoice(
    tenant_id: uuid.UUID, *, total: Decimal = Decimal("400.00"), amount_due: Decimal | None = None,
) -> tuple[Customer, Invoice]:
    from app.models.finance import InvoiceLineItem

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Invoice Payment Customer", email="invpay@example.com")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"IPS-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=total, total=total, amount_due=amount_due if amount_due is not None else total,
        )
        session.add(invoice)
        await session.flush()
        session.add(InvoiceLineItem(
            tenant_id=tenant_id, invoice_id=invoice.id, description="Service", quantity=Decimal("1"),
            unit_price=total, line_total=total,
        ))
        await session.commit()
        await session.refresh(customer)
        await session.refresh(invoice)
    return customer, invoice


async def _sync_invoice_to_quickbooks(connection_service, tenant_id: uuid.UUID, invoice_id: uuid.UUID, monkeypatch) -> Invoice:
    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-1", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id="qb-inv-1", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    sync_service = QuickBooksSyncService(async_session_maker, connection_service)
    await sync_service.sync_invoice(tenant_id, invoice_id)

    async with async_session_maker() as session:
        return await session.get(Invoice, invoice_id)


async def _record_stripe_invoice_payment(
    event_bus, tenant_id: uuid.UUID, customer_id: uuid.UUID, invoice_id: uuid.UUID, amount: Decimal,
) -> Payment:
    from app.api.tool_deps_integrations import get_integration_connection_service
    payment_service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())
    payment, _dedup = await payment_service.record_payment(
        tenant_id, customer_id=customer_id, amount=amount, provider="stripe",
        external_id=f"pi_{uuid.uuid4().hex}", payment_method="card",
        allocations=[AllocationInput(invoice_id=invoice_id, amount=amount)],
    )
    return payment


async def _build_synced_invoice_and_payment(
    connection_service, event_bus, tenant_id: uuid.UUID, monkeypatch, *, amount: Decimal = Decimal("400.00"),
) -> tuple[Invoice, Payment]:
    customer, invoice = await _make_customer_and_invoice(tenant_id, total=amount)
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    invoice = await _sync_invoice_to_quickbooks(connection_service, tenant_id, invoice.id, monkeypatch)
    payment = await _record_stripe_invoice_payment(event_bus, tenant_id, customer.id, invoice.id, amount)
    return invoice, payment


def _payment_sync_service(connection_service) -> QuickBooksPaymentSyncService:
    return QuickBooksPaymentSyncService(async_session_maker, connection_service)


# --- 1. SERVICE: sync_invoice_payment_to_quickbooks — success + eligibility. ---


async def test_sync_unknown_payment_raises_not_found(connection_service) -> None:
    service = _payment_sync_service(connection_service)
    with pytest.raises(PaymentNotFoundError):
        await service.sync_invoice_payment_to_quickbooks(uuid.uuid4(), uuid.uuid4())


async def test_deposit_payment_rejected_by_invoice_sync_method(connection_service, tool_registry, event_bus) -> None:
    """A quote-deposit payment must be synced via sync_deposit_payment,
    not this method — proves the two paths stay cleanly separated."""
    from app.services.quote_service import QuoteService

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_result = await tool_registry.execute(
        "crm.create_customer", {"name": "Deposit Not Invoice Customer", "email": "d@example.com"}, ctx
    )
    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_result.customer["id"], "line_items": [{"description": "x", "quantity": "1", "unit_price": "100.00"}],
         "deposit_type": "FIXED", "deposit_value": "50.00"},
        ctx,
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    quote_id = uuid.UUID(sent.quote["id"])
    quote_service = QuoteService(async_session_maker, event_bus)
    await quote_service.decide(tenant_id, quote_id, accepted=True)

    from app.api.tool_deps_integrations import get_integration_connection_service
    payment_service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())
    payment, _ = await payment_service.record_payment(
        tenant_id, customer_id=uuid.UUID(customer_result.customer["id"]), amount=Decimal("50.00"),
        provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", payment_method="card",
        allocations=[], quote_id=quote_id,
    )

    service = _payment_sync_service(connection_service)
    with pytest.raises(NotAnInvoicePaymentError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_invoice_payment_rejected_by_deposit_sync_method(connection_service, event_bus, monkeypatch) -> None:
    """The symmetric proof — an ordinary invoice payment must be rejected
    by sync_deposit_payment (unchanged Phase 17 method)."""
    tenant_id = uuid.uuid4()
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)
    async with async_session_maker() as session:
        row = await session.get(Payment, payment.id)
        row.quickbooks_payment_id = None  # never actually synced yet
        await session.commit()

    service = _payment_sync_service(connection_service)
    with pytest.raises(NotADepositPaymentError):
        await service.sync_deposit_payment(tenant_id, payment.id)


async def test_non_stripe_payment_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    async with async_session_maker() as session:
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="internal_test", external_id=f"tst_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("100.00")))
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(NotAStripePaymentError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_unpaid_payment_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    async with async_session_maker() as session:
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.PENDING,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("100.00")))
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(PaymentNotSucceededError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_payment_with_no_allocation_is_rejected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="No Allocation Customer")
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
    with pytest.raises(NoInvoiceAssociatedError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def _make_second_invoice(tenant_id: uuid.UUID, customer_id: uuid.UUID, *, total: Decimal) -> Invoice:
    async with async_session_maker() as session:
        invoice_b = Invoice(
            tenant_id=tenant_id, customer_id=customer_id, invoice_number=f"IPS-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=total, total=total, amount_due=total,
        )
        session.add(invoice_b)
        await session.commit()
        await session.refresh(invoice_b)
    return invoice_b


async def test_split_payment_across_two_invoices_syncs_as_one_qbo_payment_with_two_lines(
    connection_service, monkeypatch
) -> None:
    """Phase 20: a single Klaros Payment allocated across two invoices —
    a real, reachable shape PaymentService.record_payment already
    supports — now syncs as ONE QuickBooks Payment with two Line entries,
    each independently linked to its own already-synced QBO Invoice."""
    tenant_id = uuid.uuid4()
    customer, invoice_a = await _make_customer_and_invoice(tenant_id, total=Decimal("50.00"))
    invoice_b = await _make_second_invoice(tenant_id, customer.id, total=Decimal("50.00"))

    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-shared", DisplayName=display_name)

    call_n = {"n": 0}

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        call_n["n"] += 1
        return QuickBooksInvoiceResponse(Id=f"qb-inv-{call_n['n']}", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)
    sync_service = QuickBooksSyncService(async_session_maker, connection_service)
    await sync_service.sync_invoice(tenant_id, invoice_a.id)
    await sync_service.sync_invoice(tenant_id, invoice_b.id)

    async with async_session_maker() as session:
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice_a.id, amount=Decimal("50.00")))
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice_b.id, amount=Decimal("50.00")))
        await session.commit()
        await session.refresh(payment)

    captured = {}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        captured["customer_id"] = customer_id
        captured["invoice_lines"] = invoice_lines
        captured["request_id"] = request_id
        return QuickBooksPaymentResponse(Id="qb-pay-split", TotalAmt=sum(a for _i, a in invoice_lines))

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    result = await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)

    assert result.already_synced is False
    assert result.quickbooks_payment_id == "qb-pay-split"
    assert captured["customer_id"] == "qb-cust-shared"
    assert sorted(captured["invoice_lines"]) == sorted([("qb-inv-1", 50.0), ("qb-inv-2", 50.0)])
    assert captured["request_id"] == f"klaros-invoice-payment-{payment.id}"

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == "qb-pay-split"


async def test_split_payment_fails_cleanly_when_one_of_several_invoices_is_not_yet_synced(
    connection_service, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    customer, invoice_a = await _make_customer_and_invoice(tenant_id, total=Decimal("50.00"))
    invoice_b = await _make_second_invoice(tenant_id, customer.id, total=Decimal("50.00"))

    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    invoice_a = await _sync_invoice_to_quickbooks(connection_service, tenant_id, invoice_a.id, monkeypatch)
    # invoice_b deliberately never synced to QuickBooks.

    async with async_session_maker() as session:
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice_a.id, amount=Decimal("50.00")))
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice_b.id, amount=Decimal("50.00")))
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(InvoiceNotYetSyncedError, match=invoice_b.invoice_number):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_split_payment_across_invoices_of_different_customers_is_rejected(connection_service, monkeypatch) -> None:
    """A single QBO Payment has exactly one CustomerRef — if the
    allocated invoices genuinely belong to different customers (should
    never happen through real Klaros code paths, but the service must
    not silently pick one), it must fail cleanly rather than sync under
    the wrong customer."""
    tenant_id = uuid.uuid4()
    customer_a, invoice_a = await _make_customer_and_invoice(tenant_id, total=Decimal("50.00"))
    async with async_session_maker() as session:
        customer_b = Customer(tenant_id=tenant_id, name="Second Customer")
        session.add(customer_b)
        await session.commit()
        await session.refresh(customer_b)
    invoice_b = await _make_second_invoice(tenant_id, customer_b.id, total=Decimal("50.00"))

    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    call_n = {"n": 0}

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        call_n["n"] += 1
        return QuickBooksCustomerResponse(Id=f"qb-cust-{call_n['n']}", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id=f"qb-inv-{doc_number}", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)
    sync_service = QuickBooksSyncService(async_session_maker, connection_service)
    await sync_service.sync_invoice(tenant_id, invoice_a.id)
    await sync_service.sync_invoice(tenant_id, invoice_b.id)

    async with async_session_maker() as session:
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer_a.id, amount=Decimal("100.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice_a.id, amount=Decimal("50.00")))
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice_b.id, amount=Decimal("50.00")))
        await session.commit()
        await session.refresh(payment)

    service = _payment_sync_service(connection_service)
    with pytest.raises(AllocationSpansMultipleCustomersError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_invoice_not_yet_synced_is_rejected(connection_service, event_bus) -> None:
    tenant_id = uuid.uuid4()
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    payment = await _record_stripe_invoice_payment(event_bus, tenant_id, customer.id, invoice.id, Decimal("400.00"))

    service = _payment_sync_service(connection_service)
    with pytest.raises(InvoiceNotYetSyncedError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_customer_not_synced_is_rejected(connection_service, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)
    async with async_session_maker() as session:
        customer = await session.get(Customer, invoice.customer_id)
        customer.external_provider = None
        customer.external_id = None
        await session.commit()

    service = _payment_sync_service(connection_service)
    with pytest.raises(CustomerNotSyncedError):
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_without_quickbooks_connection_is_rejected(connection_service, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)
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
        await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)


async def test_full_sync_creates_qbo_payment_and_persists_id(connection_service, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    captured = {}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        captured["customer_id"] = customer_id
        captured["invoice_id"] = invoice_id
        captured["amount"] = amount
        captured["request_id"] = request_id
        return QuickBooksPaymentResponse(Id="qb-invpay-real", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    result = await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)

    assert result.already_synced is False
    assert result.quickbooks_payment_id == "qb-invpay-real"
    assert captured["customer_id"] == "qb-cust-1"
    assert captured["invoice_id"] == "qb-inv-1"
    assert captured["amount"] == 400.0
    assert captured["request_id"] == f"klaros-invoice-payment-{payment.id}"

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == "qb-invpay-real"


async def test_401_during_create_payment_refreshes_token_once_and_retries(connection_service, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    calls = {"n": 0}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        calls["n"] += 1
        if calls["n"] == 1:
            raise QuickBooksAPIError("Token expired", error_type=QuickBooksErrorType.AUTHENTICATION)
        assert access_token == "at_refreshed"
        return QuickBooksPaymentResponse(Id="qb-invpay-refreshed", TotalAmt=amount)

    async def _fake_refresh(self, *, refresh_token):
        return QuickBooksTokenResponse(access_token="at_refreshed", refresh_token="rt_refreshed", expires_in=3600)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)
    monkeypatch.setattr(QuickBooksClient, "refresh_access_token", _fake_refresh)

    service = _payment_sync_service(connection_service)
    result = await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)
    assert result.quickbooks_payment_id == "qb-invpay-refreshed"
    assert calls["n"] == 2


# --- 2. IDEMPOTENCY. ---


async def test_already_synced_payment_is_a_safe_noop_no_api_call(connection_service, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)
    async with async_session_maker() as session:
        row = await session.get(Payment, payment.id)
        row.quickbooks_payment_id = "already-synced-qb-invpay"
        await session.commit()

    async def _fail_if_called(self, **kwargs):
        raise AssertionError("must not call create_payment for an already-synced Payment")

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fail_if_called)

    service = _payment_sync_service(connection_service)
    result = await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)
    assert result.already_synced is True
    assert result.quickbooks_payment_id == "already-synced-qb-invpay"


async def test_duplicate_invocation_never_creates_two_qbo_payments(connection_service, event_bus, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    call_count = {"n": 0}

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        call_count["n"] += 1
        return QuickBooksPaymentResponse(Id="qb-invpay-once", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    result_1 = await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)
    result_2 = await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)
    assert call_count["n"] == 1
    assert result_1.quickbooks_payment_id == result_2.quickbooks_payment_id == "qb-invpay-once"
    assert result_1.already_synced is False
    assert result_2.already_synced is True


async def test_requestid_deterministic_across_simulated_partial_completion_retry(
    connection_service, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    seen_request_ids: list[str | None] = []

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        seen_request_ids.append(request_id)
        return QuickBooksPaymentResponse(Id="qb-invpay-dedup", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    service = _payment_sync_service(connection_service)
    await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)

    async with async_session_maker() as session:
        row = await session.get(Payment, payment.id)
        row.quickbooks_payment_id = None  # simulate "crashed before persisting"
        await session.commit()

    await service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)

    assert len(seen_request_ids) == 2
    assert seen_request_ids[0] == seen_request_ids[1] == f"klaros-invoice-payment-{payment.id}"
    # Also distinct in namespace from a deposit payment's requestid.
    assert not seen_request_ids[0].startswith("klaros-deposit-payment-")


# --- 3. TENANT ISOLATION. ---


async def test_tenant_b_cannot_sync_tenant_as_payment(connection_service, event_bus, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_a, monkeypatch)

    service = _payment_sync_service(connection_service)
    with pytest.raises(PaymentNotFoundError):
        await service.sync_invoice_payment_to_quickbooks(tenant_b, payment.id)


async def test_tenant_b_cannot_use_tenant_as_invoice(connection_service, event_bus, monkeypatch) -> None:
    """A forged/guessed cross-tenant invoice_id in a PaymentAllocation
    (should never happen through real code paths, but proves the lookup
    itself is tenant-scoped, not just the initial Payment check)."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _customer_a, invoice_a = await _make_customer_and_invoice(tenant_a)
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch, realm_id="realm-a")
    invoice_a = await _sync_invoice_to_quickbooks(connection_service, tenant_a, invoice_a.id, monkeypatch)

    async with async_session_maker() as session:
        customer_b = Customer(tenant_id=tenant_b, name="Tenant B Customer")
        session.add(customer_b)
        await session.flush()
        payment_b = Payment(
            tenant_id=tenant_b, customer_id=customer_b.id, amount=Decimal("400.00"), status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=date(2026, 1, 1),
        )
        session.add(payment_b)
        await session.flush()
        # A PaymentAllocation row is itself tenant-scoped (tenant_b) even
        # though it names tenant A's real invoice id — the service must
        # still refuse to resolve tenant A's Invoice for tenant B's call.
        session.add(PaymentAllocation(tenant_id=tenant_b, payment_id=payment_b.id, invoice_id=invoice_a.id, amount=Decimal("400.00")))
        await session.commit()
        await session.refresh(payment_b)

    await _connect_quickbooks(connection_service, tenant_b, monkeypatch, realm_id="realm-b")
    service = _payment_sync_service(connection_service)
    with pytest.raises(InvoiceNotYetCreatedError):
        await service.sync_invoice_payment_to_quickbooks(tenant_b, payment_b.id)


async def test_tenant_b_cannot_use_tenant_as_quickbooks_connection(connection_service, event_bus, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_a, monkeypatch, realm_id="realm-a")

    _invoice_b, payment_b = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_b, monkeypatch)
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

    service = _payment_sync_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.sync_invoice_payment_to_quickbooks(tenant_b, payment_b.id)


# --- 4. EVENT / WORKER integration (reuses EventType.PAYMENT_RECEIVED). ---


async def test_payment_received_event_triggers_automatic_sync_for_invoice_payment(
    connection_service, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-invpay-from-event", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_RECEIVED, source="test",
        entity_type="payment", entity_id=payment.id,
        payload={"payment_id": str(payment.id), "amount": str(payment.amount), "invoice_ids": [str(invoice.id)]},
    )
    stats = await event_bus.process_pending(EventType.PAYMENT_RECEIVED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == "qb-invpay-from-event"

        record = (
            await session.execute(
                select(EventProcessingRecord).where(
                    EventProcessingRecord.event_id == event.id,
                    EventProcessingRecord.handler_name == "finance_quickbooks_invoice_payment_sync",
                )
            )
        ).scalar_one()
        assert record.status == ProcessingStatus.SUCCESS


async def test_payment_received_event_for_deposit_payment_is_a_silent_noop_here(
    connection_service, tool_registry, event_bus
) -> None:
    """A deposit payment ALSO publishes PAYMENT_RECEIVED — this handler
    must silently skip it (no error, no dead-letter) since that's the
    OTHER handler's job (finance_quickbooks_deposit_payment_sync)."""
    from app.services.quote_service import QuoteService

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_result = await tool_registry.execute(
        "crm.create_customer", {"name": "Deposit PR Customer", "email": "dpr@example.com"}, ctx
    )
    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_result.customer["id"], "line_items": [{"description": "x", "quantity": "1", "unit_price": "100.00"}],
         "deposit_type": "FIXED", "deposit_value": "50.00"},
        ctx,
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    quote_id = uuid.UUID(sent.quote["id"])
    quote_service = QuoteService(async_session_maker, event_bus)
    await quote_service.decide(tenant_id, quote_id, accepted=True)

    from app.api.tool_deps_integrations import get_integration_connection_service
    payment_service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())
    payment, _ = await payment_service.record_payment(
        tenant_id, customer_id=uuid.UUID(customer_result.customer["id"]), amount=Decimal("50.00"),
        provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", payment_method="card",
        allocations=[], quote_id=quote_id,
    )

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_RECEIVED, source="test",
        entity_type="payment", entity_id=payment.id, payload={"payment_id": str(payment.id)},
    )
    stats = await event_bus.process_pending(EventType.PAYMENT_RECEIVED)
    assert stats.dead_lettered == 0
    assert stats.failed_retrying == 0

    async with async_session_maker() as session:
        record = (
            await session.execute(
                select(EventProcessingRecord).where(
                    EventProcessingRecord.event_id == event.id,
                    EventProcessingRecord.handler_name == "finance_quickbooks_invoice_payment_sync",
                )
            )
        ).scalar_one()
        assert record.status == ProcessingStatus.SUCCESS  # no-op counts as success, not failure

        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id is None  # untouched — deposit path owns this payment


async def test_failed_automatic_invoice_payment_sync_lands_in_dead_letter_without_mutating_state(
    connection_service, event_bus
) -> None:
    """The realistic common case: the payment succeeded but the invoice
    hasn't been synced to QuickBooks yet — the automatic attempt fails
    cleanly (not transient), exhausts retries, dead-letters, and touches
    NEITHER Payment nor Invoice state."""
    tenant_id = uuid.uuid4()
    event_bus.max_retries = 1
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    payment = await _record_stripe_invoice_payment(event_bus, tenant_id, customer.id, invoice.id, Decimal("400.00"))

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_RECEIVED, source="test",
        entity_type="payment", entity_id=payment.id, payload={"payment_id": str(payment.id)},
    )
    stats = await event_bus.process_pending(EventType.PAYMENT_RECEIVED)
    assert stats.dead_lettered >= 1

    async with async_session_maker() as session:
        dl = (
            await session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.event_id == event.id,
                    DeadLetterEvent.handler_name == "finance_quickbooks_invoice_payment_sync",
                )
            )
        ).scalar_one()
        assert "synced" in dl.reason.lower() or "quickbooks" in dl.reason.lower()

        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_payment.status == PaymentStatus.SUCCEEDED
        assert refreshed_payment.quickbooks_payment_id is None
        refreshed_invoice = await session.get(Invoice, invoice.id)
        assert refreshed_invoice.status == InvoiceStatus.PAID  # unaffected by the sync failure


async def test_replaying_dead_lettered_invoice_payment_sync_succeeds_once_invoice_is_synced(
    connection_service, event_bus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    event_bus.max_retries = 1
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    payment = await _record_stripe_invoice_payment(event_bus, tenant_id, customer.id, invoice.id, Decimal("400.00"))

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.PAYMENT_RECEIVED, source="test",
        entity_type="payment", entity_id=payment.id, payload={"payment_id": str(payment.id)},
    )
    await event_bus.process_pending(EventType.PAYMENT_RECEIVED)  # dead-letters, invoice not synced yet

    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    await _sync_invoice_to_quickbooks(connection_service, tenant_id, invoice.id, monkeypatch)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-invpay-replayed", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    result = await event_bus.replay(event.id, "finance_quickbooks_invoice_payment_sync")
    assert result == ProcessingStatus.SUCCESS

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.quickbooks_payment_id == "qb-invpay-replayed"


# --- 5. REFUND COMPATIBILITY: full internal chain, zero refund-service changes needed. ---


async def test_refund_against_a_synced_invoice_payment_syncs_via_existing_refund_service(
    connection_service, event_bus, monkeypatch
) -> None:
    """Payment -> QuickBooks Payment -> Refund -> QuickBooks RefundReceipt,
    entirely through Phase 18's UNMODIFIED QuickBooksRefundSyncService —
    proves it needed zero changes to support an ordinary-invoice-payment
    origin (it only ever checked Payment.quickbooks_payment_id, never
    quote_id)."""
    tenant_id = uuid.uuid4()
    invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-invpay-for-refund", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)
    payment_sync_service = _payment_sync_service(connection_service)
    await payment_sync_service.sync_invoice_payment_to_quickbooks(tenant_id, payment.id)

    async def _fake_stripe_create_refund(self, *, payment_intent_id, amount=None, reason=None, idempotency_key=None):
        return StripeRefundResponse(id=f"re_{uuid.uuid4().hex}", status="succeeded", amount=int((amount or 0) * 100), currency="usd")

    monkeypatch.setattr(StripeClient, "create_refund", _fake_stripe_create_refund)

    from app.api.tool_deps_integrations import get_integration_connection_service
    payment_service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())
    refund = await payment_service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("150.00"),
        reason="Partial refund", requested_by=None,
    )
    refund = await payment_service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)
    assert refund.status == "COMPLETED"

    async def _fake_create_refund_receipt(self, *, access_token, realm_id, customer_id, payment_id, amount, request_id=None):
        assert payment_id == "qb-invpay-for-refund"
        return QuickBooksRefundReceiptResponse(Id="qb-refund-for-invpay", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_refund_receipt", _fake_create_refund_receipt)

    refund_sync_service = QuickBooksRefundSyncService(async_session_maker, connection_service)
    result = await refund_sync_service.sync_refund_to_quickbooks(tenant_id, refund.id)
    assert result.quickbooks_refund_receipt_id == "qb-refund-for-invpay"

    async with async_session_maker() as session:
        refreshed_refund = await session.get(Refund, refund.id)
        assert refreshed_refund.quickbooks_refund_receipt_id == "qb-refund-for-invpay"


# --- 6. TOOL: RBAC. ---


async def test_technician_cannot_call_invoice_payment_sync_tool(tool_registry) -> None:
    from app.tools.errors import ToolError

    tenant_id = uuid.uuid4()
    tech_ctx = _ctx(tenant_id, role=Role.TECHNICIAN)
    with pytest.raises(ToolError):
        await tool_registry.execute(
            "finance.sync_invoice_payment_to_quickbooks", {"payment_id": str(uuid.uuid4())}, tech_ctx
        )


async def test_owner_can_sync_via_tool(connection_service, event_bus, monkeypatch, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    _invoice, payment = await _build_synced_invoice_and_payment(connection_service, event_bus, tenant_id, monkeypatch)

    async def _fake_create_payment(self, *, access_token, realm_id, customer_id, invoice_lines, request_id=None):
        invoice_id, amount = invoice_lines[0]
        return QuickBooksPaymentResponse(Id="qb-invpay-tool", TotalAmt=amount)

    monkeypatch.setattr(QuickBooksClient, "create_payment", _fake_create_payment)

    result = await tool_registry.execute(
        "finance.sync_invoice_payment_to_quickbooks", {"payment_id": str(payment.id)}, ctx
    )
    assert result.quickbooks_payment_id == "qb-invpay-tool"
    assert result.already_synced is False
