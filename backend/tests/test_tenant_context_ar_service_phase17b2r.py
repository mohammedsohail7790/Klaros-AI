"""Phase 17B-2R: real-PostgreSQL behavioral proof that ARService's own,
independently-opened sessions (4 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from datetime import date, timedelta
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
from app.services.ar_service import ARService
from app.services.collection_service import CollectionService
from app.services.exception_service import ExceptionService

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


async def _make_org_customer_overdue_invoice(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="AR Test Customer"))
        session.add(
            Invoice(
                tenant_id=tenant_id, customer_id=customer_id, invoice_number=f"AR-{uuid.uuid4().hex[:8]}",
                status=InvoiceStatus.SENT, issue_date=date.today() - timedelta(days=60),
                due_date=date.today() - timedelta(days=10), subtotal=Decimal("100"), total=Decimal("100"),
                amount_paid=Decimal("0"), amount_due=Decimal("100"),
            )
        )
        await session.commit()
    return customer_id


def _service() -> ARService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return ARService(
        async_session_maker, ExceptionService(async_session_maker, bus), CollectionService(async_session_maker),
    )


@requires_real_postgres
async def test_aging_summary_and_detect_overdue_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.ar_service as ar_service_module

    monkeypatch.setattr(ar_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org_customer_overdue_invoice(tenant_id)
    service = _service()

    await service.aging_summary(tenant_id)
    await service.detect_overdue(tenant_id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_aging_summary_never_sees_tenant_bs_invoices() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org_customer_overdue_invoice(tenant_a)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()

    service = _service()
    summary_b = await service.aging_summary(tenant_b)
    assert summary_b.total == 0

    summary_a = await service.aging_summary(tenant_a)
    assert summary_a.total > 0
