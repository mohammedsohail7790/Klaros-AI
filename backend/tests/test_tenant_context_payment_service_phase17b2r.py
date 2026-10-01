"""Phase 17B-2R: real-PostgreSQL behavioral proof that PaymentService's own,
independently-opened sessions (11 sites) now stamp `SET LOCAL
app.tenant_id`. Special care per task §13: this test never touches Stripe
webhook signature verification and never trusts a client-supplied
tenant_id — `tenant_id` here is always the caller-trusted parameter,
exactly as production call sites (authenticated routes, verified webhook
handlers) already provide it. `record_payment`/`reconcile_external_refund`
are exercised directly (no real Stripe call involved), keeping this test
independent of Stripe client mocking.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.payment_service import AllocationInput, PaymentService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _make_org_customer_invoice(tenant_id: uuid.UUID, *, amount: Decimal = Decimal("100.00")):
    customer_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Payment Test Customer"))
        session.add(
            Invoice(
                id=invoice_id, tenant_id=tenant_id, customer_id=customer_id, invoice_number=f"PSP-{uuid.uuid4().hex[:8]}",
                status=InvoiceStatus.SENT, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                subtotal=amount, total=amount, amount_paid=Decimal("0"), amount_due=amount,
            )
        )
        await session.commit()
    return customer_id, invoice_id


def _service() -> PaymentService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return PaymentService(async_session_maker, bus, connection_service=None)


@requires_real_postgres
async def test_record_payment_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.payment_service as payment_service_module

    monkeypatch.setattr(payment_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    customer_id, invoice_id = await _make_org_customer_invoice(tenant_id)
    service = _service()

    payment, deduped = await service.record_payment(
        tenant_id, customer_id=customer_id, amount=Decimal("100.00"), provider="manual",
        external_id=f"pay-{uuid.uuid4().hex[:8]}", payment_method="cash",
        allocations=[AllocationInput(invoice_id=invoice_id, amount=Decimal("100.00"))],
    )
    assert not deduped

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_reconcile_external_refund_is_tenant_scoped(monkeypatch, spy) -> None:
    import app.services.payment_service as payment_service_module

    monkeypatch.setattr(payment_service_module, "set_tenant_context", spy)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a, invoice_a = await _make_org_customer_invoice(tenant_a)
    service = _service()

    external_id = f"pi_{uuid.uuid4().hex}"
    await service.record_payment(
        tenant_a, customer_id=customer_a, amount=Decimal("100.00"), provider="stripe",
        external_id=external_id, payment_method=None,
        allocations=[AllocationInput(invoice_id=invoice_a, amount=Decimal("100.00"))],
    )

    # Tenant B reconciling against tenant A's own external payment id must
    # find nothing — real cross-tenant isolation, not merely a permission
    # check layered on top.
    refund, error = await service.reconcile_external_refund(
        tenant_b, provider="stripe", external_payment_id=external_id,
        total_amount_refunded=Decimal("50.00"), reason="test",
    )
    assert refund is None
    assert error is not None

    for called_tenant, readback in spy.calls:
        assert readback == str(called_tenant)
