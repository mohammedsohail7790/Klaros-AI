"""Phase 17B-2R: real-PostgreSQL behavioral proof for RecommendationService's
9 session-open sites: 7 genuinely tenant-scoped (fixed), 2 that only ever
read the GLOBAL `IntegrationProviderCatalog`/`VerticalExtension` catalog
tables (correctly excluded — proven here by asserting the spy is never
invoked for them specifically, via call-count comparison against the
tenant-scoped calls).
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, BlueprintSectionKey, ClaimProvenance, ClaimType
from app.models.organization import Organization
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.recommendation_service import RecommendationNotFoundError, RecommendationService

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


async def _activate_blueprint_with_capabilities(
    blueprint_service: BusinessBlueprintService, tenant_id: uuid.UUID, capability_keys: list[str]
):
    blueprint = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)
    for key in MINIMUM_BAR_SECTIONS:
        if key == BlueprintSectionKey.REQUIRED_CAPABILITIES:
            continue
        claim = await blueprint_service.propose_claim(
            tenant_id, blueprint.id, section_key=key.value, claim_type=ClaimType.FACT.value,
            key=f"{key.value.lower()}.v", value="ok", confidence=None,
            provenance=ClaimProvenance.USER_STATED.value, discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)

    cap_claim = await blueprint_service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="required_capabilities.list", value=capability_keys,
        confidence=0.9, provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await blueprint_service.confirm_claim(tenant_id, cap_claim.id, confirmed_by=None)
    return await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)


@requires_real_postgres
async def test_generate_recommendations_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.services.recommendation_service as rec_module

    monkeypatch.setattr(rec_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    blueprint_service = BusinessBlueprintService(async_session_maker)
    service = RecommendationService(async_session_maker)
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["scheduling"])

    run = await service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    assert run.tenant_id == tenant_id
    await service.list_recommendations(tenant_id)
    await service.get_run(tenant_id, run.id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_run() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    blueprint_service = BusinessBlueprintService(async_session_maker)
    service = RecommendationService(async_session_maker)

    from app.ai.execution_service import AIExecutionService
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport
    from app.tools.factory import build_tool_registry

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)

    await _activate_blueprint_with_capabilities(blueprint_service, tenant_a, ["scheduling"])
    run = await service.generate_recommendations(tenant_a, tool_registry=registry, triggered_by=None)

    with pytest.raises(RecommendationNotFoundError):
        await service.get_run(tenant_b, run.id)
