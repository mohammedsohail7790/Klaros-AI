"""Phase 17B-2R: real-PostgreSQL behavioral proof that VendorService's own,
independently-opened sessions (5 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.organization import Organization
from app.services.exception_service import ExceptionService
from app.services.job_costing_service import JobCostingService
from app.services.vendor_service import VendorBillNotFoundError, VendorService

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


def _service() -> VendorService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    job_costing = JobCostingService(async_session_maker, ExceptionService(async_session_maker, bus))
    return VendorService(async_session_maker, job_costing)


@requires_real_postgres
async def test_create_vendor_and_record_bill_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.vendor_service as vendor_service_module
    from datetime import date

    monkeypatch.setattr(vendor_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    vendor = await service.create_vendor(tenant_id, name="Acme Supply", email=None, phone=None)
    bill = await service.record_bill(
        tenant_id, vendor_id=vendor.id, job_id=None, amount=Decimal("500.00"), due_date=date.today(),
    )
    await service.record_payout(tenant_id, vendor_id=vendor.id, bill_id=bill.id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_pay_tenant_bs_bill() -> None:
    from datetime import date

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    vendor = await service.create_vendor(tenant_a, name="A-only Vendor", email=None, phone=None)
    bill = await service.record_bill(
        tenant_a, vendor_id=vendor.id, job_id=None, amount=Decimal("100.00"), due_date=date.today(),
    )

    with pytest.raises(VendorBillNotFoundError):
        await service.record_payout(tenant_b, vendor_id=vendor.id, bill_id=bill.id)
