"""Phase 17B-2R: real-PostgreSQL behavioral proof that
BusinessJourneyService's own, independently-opened sessions (11 sites,
including the special `lock_session` used for the Postgres advisory lock
in `start_journey` — a genuinely distinct site from the `async with
self._session_factory()` pattern used elsewhere) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.services.ai_provider import get_ai_provider
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.business_discovery_service import BusinessDiscoveryService
from app.services.business_journey_service import BusinessJourneyNotFoundError, BusinessJourneyService
from app.services.discovery_extraction_service import DiscoveryExtractionService
from app.services.recommendation_service import RecommendationService

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


def _service() -> BusinessJourneyService:
    blueprint_service = BusinessBlueprintService(async_session_maker)
    extraction = DiscoveryExtractionService(async_session_maker, get_ai_provider())
    discovery_service = BusinessDiscoveryService(async_session_maker, blueprint_service, extraction)
    recommendation_service = RecommendationService(async_session_maker)
    return BusinessJourneyService(async_session_maker, discovery_service, blueprint_service, recommendation_service)


@requires_real_postgres
async def test_start_journey_sets_tenant_context_including_lock_session(monkeypatch, spy) -> None:
    import app.services.business_journey_service as bjs_module

    monkeypatch.setattr(bjs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    service = _service()

    result = await service.start_journey(tenant_id, business_idea="A landscaping business", created_by=None)
    assert result.created
    await service.get_current(tenant_id)

    # get_current (called internally by start_journey, then again
    # explicitly) + the advisory-lock session + the journey-insert session.
    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_journey() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    service = _service()

    result = await service.start_journey(tenant_a, business_idea="A-only business", created_by=None)

    with pytest.raises(BusinessJourneyNotFoundError):
        await service.get_by_id(tenant_b, result.journey.id)

    assert await service.get_current(tenant_b) is None
