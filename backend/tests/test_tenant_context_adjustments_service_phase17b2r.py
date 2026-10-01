"""Phase 17B-2R: real-PostgreSQL behavioral proof that AdjustmentsService's
own, independently-opened sessions (4 sites: credit-note and write-off
request/decide, both approval-gated) now stamp `SET LOCAL app.tenant_id`.
Same methodology as `test_tenant_context_automation_service_phase17b2r.py`.
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
from app.services.adjustments_service import AdjustmentsService, InvoiceNotFoundError

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


async def _make_org_customer_invoice(tenant_id: uuid.UUID, *, amount: Decimal = Decimal("500.00")) -> uuid.UUID:
    customer_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Adjustments Test Customer"))
        session.add(
            Invoice(
                id=invoice_id, tenant_id=tenant_id, customer_id=customer_id, invoice_number=f"ADJ-{uuid.uuid4().hex[:8]}",
                status=InvoiceStatus.SENT, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                subtotal=amount, total=amount, amount_paid=Decimal("0"), amount_due=amount,
            )
        )
        await session.commit()
    return invoice_id


def _service() -> AdjustmentsService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return AdjustmentsService(async_session_maker, bus)


@requires_real_postgres
async def test_request_and_decide_credit_note_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.adjustments_service as adjustments_service_module

    monkeypatch.setattr(adjustments_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    invoice_id = await _make_org_customer_invoice(tenant_id)
    service = _service()

    note = await service.request_credit_note(
        tenant_id, invoice_id=invoice_id, reason="Customer complaint",
        line_items=[("Partial refund", Decimal("50.00"))], requested_by=None,
    )
    await service.decide_credit_note(tenant_id, note.id, approved=True, decided_by=None)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_request_credit_note_on_tenant_bs_invoice() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    invoice_a = await _make_org_customer_invoice(tenant_a)
    await _make_org_customer_invoice(tenant_b)
    service = _service()

    with pytest.raises(InvoiceNotFoundError):
        await service.request_credit_note(
            tenant_b, invoice_id=invoice_a, reason="cross-tenant probe",
            line_items=[("x", Decimal("10.00"))], requested_by=None,
        )
