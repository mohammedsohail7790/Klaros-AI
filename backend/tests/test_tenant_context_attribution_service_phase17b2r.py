"""Phase 17B-2R: real-PostgreSQL behavioral proof that AttributionService's
own, independently-opened sessions (`self._session_factory()`, 5 sites) now
stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Lead
from app.models.organization import Organization
from app.services.attribution_service import AttributionService, LeadNotFoundError

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


async def _make_org_and_lead(tenant_id: uuid.UUID) -> uuid.UUID:
    lead_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Lead(id=lead_id, tenant_id=tenant_id, name="Test Lead", source="referral"))
        await session.commit()
    return lead_id


@requires_real_postgres
async def test_attribute_lead_and_advance_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.attribution_service as attribution_service_module

    monkeypatch.setattr(attribution_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    lead_id = await _make_org_and_lead(tenant_id)
    service = AttributionService(async_session_maker)

    await service.attribute_lead(tenant_id, lead_id, source="google", medium="cpc")
    await service.mark_qualified(tenant_id, lead_id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_attribute_tenant_bs_lead() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_a = await _make_org_and_lead(tenant_a)
    await _make_org_and_lead(tenant_b)
    service = AttributionService(async_session_maker)

    with pytest.raises(LeadNotFoundError):
        await service.attribute_lead(tenant_b, lead_a, source="cross-tenant-probe")


@requires_real_postgres
async def test_pool_reuse_never_leaks_attribution_tenant_context() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_a = await _make_org_and_lead(tenant_a)
    lead_b = await _make_org_and_lead(tenant_b)
    service = AttributionService(async_session_maker)

    for _ in range(2):
        await service.attribute_lead(tenant_a, lead_a, source="a")
        await service.attribute_lead(tenant_b, lead_b, source="b")
        with pytest.raises(LeadNotFoundError):
            await service.attribute_lead(tenant_a, lead_b, source="cross-check")
        with pytest.raises(LeadNotFoundError):
            await service.attribute_lead(tenant_b, lead_a, source="cross-check")
