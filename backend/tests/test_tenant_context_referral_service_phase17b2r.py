"""Phase 17B-2R: real-PostgreSQL behavioral proof that ReferralService's
own, independently-opened sessions (11 sites) now stamp `SET LOCAL
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
from app.services.attribution_service import AttributionService
from app.services.campaign_service import CampaignService
from app.services.exception_service import ExceptionService
from app.services.lead_service import LeadService
from app.services.referral_service import ProgramNotFoundError, ReferralService

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


def _service() -> ReferralService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    campaign_service = CampaignService(async_session_maker, bus, ExceptionService(async_session_maker, bus))
    attribution_service = AttributionService(async_session_maker)
    lead_service = LeadService(async_session_maker, bus)
    return ReferralService(async_session_maker, bus, campaign_service, attribution_service, lead_service)


@requires_real_postgres
async def test_create_program_and_code_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.referral_service as referral_service_module

    monkeypatch.setattr(referral_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    program = await service.create_program(tenant_id, name="Refer a Friend")
    customer_id = uuid.uuid4()
    code = await service.get_or_create_code(tenant_id, program.id, customer_id)
    referral = await service.create_referral(tenant_id, code.id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
    assert referral.tenant_id == tenant_id


@requires_real_postgres
async def test_tenant_a_cannot_use_tenant_bs_program() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    program_a = await service.create_program(tenant_a, name="A-only Program")

    with pytest.raises(ProgramNotFoundError):
        await service.get_or_create_code(tenant_b, program_a.id, uuid.uuid4())
