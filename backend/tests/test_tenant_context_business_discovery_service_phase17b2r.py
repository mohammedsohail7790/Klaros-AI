"""Phase 17B-2R: re-verification pass over Phase 17B-2's own diff —
`business_discovery_service.py` appeared in Phase 17B-2's git diff (per
the working-tree status at the start of 17B-2R), but that diff was Phase
16B's discovery-fallback work, unrelated to tenant-context propagation;
none of this file's 6 `self._session_factory()` sites actually called
`set_tenant_context` until this round. Closes that gap — real-PostgreSQL
proof the same way as every other service this phase.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.services.ai_provider import get_ai_provider
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.business_discovery_service import BusinessDiscoveryService, DiscoverySessionNotFoundError
from app.services.discovery_extraction_service import DiscoveryExtractionService

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


def _service() -> BusinessDiscoveryService:
    blueprint_service = BusinessBlueprintService(async_session_maker)
    extraction = DiscoveryExtractionService(async_session_maker, get_ai_provider())
    return BusinessDiscoveryService(async_session_maker, blueprint_service, extraction)


@requires_real_postgres
async def test_start_session_and_submit_answer_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.business_discovery_service as bds_module

    monkeypatch.setattr(bds_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    service = _service()

    result = await service.start_session(tenant_id, business_idea="A mobile dog grooming business", created_by=None)
    assert result.session.tenant_id == tenant_id

    await service.submit_answer(
        tenant_id, result.session.id, answer="We serve the greater Austin area.", actor_id=None,
    )

    assert len(spy.calls) >= 4  # start_session's own session + _process_turn's session, x2 turns
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_submit_answer_to_tenant_bs_session() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    service = _service()

    result = await service.start_session(tenant_a, business_idea="A-only business", created_by=None)

    with pytest.raises(DiscoverySessionNotFoundError):
        await service.submit_answer(tenant_b, result.session.id, answer="cross-tenant probe", actor_id=None)
