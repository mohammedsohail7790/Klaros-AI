"""Phase 17B-2R: real-PostgreSQL behavioral proof that CampaignService's own,
independently-opened sessions (5 sites) now stamp `SET LOCAL
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
from app.services.campaign_service import CampaignNotFoundError, CampaignService
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


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


def _service() -> CampaignService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return CampaignService(async_session_maker, bus, ExceptionService(async_session_maker, bus))


@requires_real_postgres
async def test_create_campaign_and_budget_status_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.campaign_service as campaign_service_module

    monkeypatch.setattr(campaign_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    campaign = await service.create_campaign(tenant_id, name="Spring Promo", channel="GOOGLE_ADS")
    await service.budget_status(tenant_id, campaign.id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_campaign_budget() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    campaign = await service.create_campaign(tenant_a, name="A-only Campaign", channel="GOOGLE_ADS")

    with pytest.raises(CampaignNotFoundError):
        await service.budget_status(tenant_b, campaign.id)
