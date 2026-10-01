"""Phase 17B-2R: real-PostgreSQL behavioral proof that RetentionService's
own, independently-opened sessions (12 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.organization import Organization
from app.services.exception_service import ExceptionService
from app.services.retention_service import RetentionService

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


def _service() -> RetentionService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return RetentionService(async_session_maker, bus, ExceptionService(async_session_maker, bus))


@requires_real_postgres
async def test_create_referral_opportunity_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.retention_service as retention_service_module

    monkeypatch.setattr(retention_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    customer_id = uuid.uuid4()
    service = _service()

    await service.create_referral_eligibility_opportunity(tenant_id, customer_id, reason="great feedback")

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_detect_at_risk_and_inactive_is_tenant_scoped(monkeypatch, spy) -> None:
    import app.services.retention_service as retention_service_module

    monkeypatch.setattr(retention_service_module, "set_tenant_context", spy)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    changed_a = await service.detect_at_risk_and_inactive(tenant_a)
    changed_b = await service.detect_at_risk_and_inactive(tenant_b)
    assert changed_a == []  # no lifecycle profiles exist yet for either tenant
    assert changed_b == []

    called_tenants = {c[0] for c in spy.calls}
    assert called_tenants == {tenant_a, tenant_b}
    for called_tenant, readback in spy.calls:
        assert readback == str(called_tenant)
