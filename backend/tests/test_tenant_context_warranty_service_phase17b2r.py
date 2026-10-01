"""Phase 17B-2R: real-PostgreSQL behavioral proof that WarrantyService's
own, independently-opened sessions (5 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.organization import Organization
from app.services.exception_service import ExceptionService
from app.services.warranty_service import WarrantyNotFoundError, WarrantyService

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


async def _make_org_and_customer(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Warranty Test Customer"))
        await session.commit()
    return customer_id


def _service() -> WarrantyService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return WarrantyService(async_session_maker, ExceptionService(async_session_maker, bus))


@requires_real_postgres
async def test_create_check_in_and_detect_expiring_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.warranty_service as warranty_service_module

    monkeypatch.setattr(warranty_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    customer_id = await _make_org_and_customer(tenant_id)
    service = _service()

    warranty = await service.create_warranty(
        tenant_id, customer_id=customer_id, job_id=None, item_description="HVAC unit",
        start_date=date.today() - timedelta(days=300), expiry_date=date.today() + timedelta(days=10), notes=None,
    )
    await service.check_in(tenant_id, warranty.id, notes="checked")
    await service.detect_expiring(tenant_id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_check_in_tenant_bs_warranty() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a = await _make_org_and_customer(tenant_a)
    await _make_org_and_customer(tenant_b)
    service = _service()

    warranty = await service.create_warranty(
        tenant_a, customer_id=customer_a, job_id=None, item_description="A-only item",
        start_date=date.today(), expiry_date=date.today() + timedelta(days=365), notes=None,
    )

    with pytest.raises(WarrantyNotFoundError):
        await service.check_in(tenant_b, warranty.id, notes=None)
