"""Phase 17B-2R: real-PostgreSQL behavioral proof that InsightService's own,
independently-opened, read-only sessions (8 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.insight_service import InsightService

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


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


@requires_real_postgres
async def test_all_snapshots_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.insight_service as insight_service_module

    monkeypatch.setattr(insight_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = InsightService(async_session_maker)

    await service.finance_snapshot(tenant_id)
    await service.operations_snapshot(tenant_id)
    await service.sales_snapshot(tenant_id)
    await service.commercial_pipeline_snapshot(tenant_id)
    await service.marketing_snapshot(tenant_id)
    await service.retention_snapshot(tenant_id)
    await service.exception_snapshot(tenant_id)
    await service.voice_snapshot(tenant_id)

    assert len(spy.calls) == 8
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_isolation_finance_snapshot_never_sees_other_tenants_data() -> None:
    from datetime import date, timezone as tz, datetime

    from app.models.crm import Customer
    from app.models.finance import Invoice, InvoiceStatus

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)

    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Customer(id=customer_id, tenant_id=tenant_a, name="A Customer"))
        session.add(
            Invoice(
                tenant_id=tenant_a, customer_id=customer_id, invoice_number="ISP-1", status=InvoiceStatus.OVERDUE,
                issue_date=date(2020, 1, 1), due_date=date(2020, 2, 1), subtotal=100, total=100,
                amount_paid=0, amount_due=100,
            )
        )
        await session.commit()

    service = InsightService(async_session_maker)
    snapshot_b = await service.finance_snapshot(tenant_b)
    assert snapshot_b.overdue_invoice_count == 0
    assert snapshot_b.total_ar == 0

    snapshot_a = await service.finance_snapshot(tenant_a)
    assert snapshot_a.overdue_invoice_count == 1
