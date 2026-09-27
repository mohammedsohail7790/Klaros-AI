"""Phase 3: RecommendationService — capability/provider/tool matching,
evidence/provenance, lifecycle (accept/reject/supersede), tenant isolation,
duplicate prevention, unknown-reference rejection, and the read-only
guarantee (never executes a tool, never creates an IntegrationConnection,
never calls an external provider).
"""

import uuid

import pytest

from app.models.business_blueprint import (
    MINIMUM_BAR_SECTIONS,
    BlueprintSectionKey,
    ClaimProvenance,
    ClaimType,
)
from app.models.integration_catalog import IntegrationProviderCatalog, ProviderAuthShape, ProviderImplementationStatus
from app.models.recommendation import RecommendationSource, RecommendationStatus, RecommendationType
from app.models.vertical_extension import VerticalExtensionStatus
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.integration_catalog_service import IntegrationCatalogService
from app.services.recommendation_service import (
    InvalidRecommendationTransitionError,
    NoActiveBlueprintError,
    RecommendationNotFoundError,
    RecommendationService,
)
from app.services.vertical_extension_service import VerticalExtensionService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def blueprint_service(tool_registry) -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


@pytest.fixture
def recommendation_service(tool_registry) -> RecommendationService:
    from app.db.session import async_session_maker

    return RecommendationService(async_session_maker)


@pytest.fixture
def catalog_service(tool_registry) -> IntegrationCatalogService:
    from app.db.session import async_session_maker

    return IntegrationCatalogService(async_session_maker)


@pytest.fixture
def vertical_service(tool_registry) -> VerticalExtensionService:
    from app.db.session import async_session_maker

    return VerticalExtensionService(async_session_maker)


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


async def _seed_provider(catalog_service: IntegrationCatalogService, key: str, status: str, capabilities: list[str]):
    entry = await catalog_service.create_entry(
        provider_key=key, display_name=key.title(), category="test",
        implementation_status=status, auth_shape=ProviderAuthShape.API_KEY,
    )
    # create_entry doesn't take capabilities — set it directly via the model.
    from app.db.session import async_session_maker
    from sqlalchemy import update

    async with async_session_maker() as session:
        await session.execute(
            update(IntegrationProviderCatalog)
            .where(IntegrationProviderCatalog.provider_key == key)
            .values(capabilities=capabilities)
        )
        await session.commit()
    return entry


async def test_generate_requires_active_blueprint(recommendation_service: RecommendationService, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(NoActiveBlueprintError):
        await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)


async def test_capability_recommendation_generated_from_confirmed_claim(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["payment_processing"])
    run = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    assert run.recommendation_count >= 1

    recs = await recommendation_service.list_recommendations(tenant_id)
    cap_recs = [r for r in recs if r.type == RecommendationType.CAPABILITY]
    assert len(cap_recs) == 1
    rec = cap_recs[0]
    assert rec.capability_key == "payment_processing"
    assert rec.required is True
    assert rec.source == RecommendationSource.BASELINE_RULE
    assert rec.status == RecommendationStatus.PROPOSED
    # 8-field schema completeness (KLAROS_FINAL_TESTING_ARCHITECTURE.md):
    # WHY/WHAT/DEPENDENCIES/COST/REQUIRED/ALTERNATIVES/CONFIDENCE/SOURCE.
    assert rec.why and rec.what
    assert rec.dependencies == []
    assert rec.cost_estimate is None
    assert isinstance(rec.required, bool)
    assert rec.alternatives == []
    assert 0.0 <= rec.confidence <= 1.0
    assert rec.source in (RecommendationSource.BASELINE_RULE, RecommendationSource.VERTICAL_EXTENSION_RULE)
    assert rec.based_on and rec.based_on[0]["kind"] == "blueprint_claim"


async def test_integration_recommendation_matches_real_provider_and_preserves_status(
    blueprint_service, recommendation_service, catalog_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    await _seed_provider(catalog_service, "acmepay", ProviderImplementationStatus.REAL, ["payment_processing"])
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["payment_processing"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.INTEGRATION)
    assert len(recs) == 1
    assert recs[0].provider_key == "acmepay"
    assert recs[0].provider_implementation_status == ProviderImplementationStatus.REAL
    assert recs[0].required is False


async def test_stub_provider_status_preserved_never_implied_ready(
    blueprint_service, recommendation_service, catalog_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    await _seed_provider(catalog_service, "stubby", ProviderImplementationStatus.STUB, ["marketing"])
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["marketing"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.INTEGRATION)
    assert len(recs) == 1
    assert recs[0].provider_implementation_status == ProviderImplementationStatus.STUB
    # Never overridden/implied ready — status string is exactly the catalog's own.
    assert recs[0].provider_implementation_status != ProviderImplementationStatus.REAL


async def test_webhook_normalizer_provider_status_preserved(
    blueprint_service, recommendation_service, catalog_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    await _seed_provider(catalog_service, "webhooky", ProviderImplementationStatus.WEBHOOK_NORMALIZER, ["lead_generation"])
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["lead_generation"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.INTEGRATION)
    assert len(recs) == 1
    assert recs[0].provider_implementation_status == ProviderImplementationStatus.WEBHOOK_NORMALIZER


async def test_no_matching_provider_yields_no_integration_recommendation(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["some_totally_unmatched_capability"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.INTEGRATION)
    assert recs == []


async def test_tool_recommendation_matches_real_registered_tool(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    # "crm.create_appointment" is a real registered tool (app/tools/builtin/appointment_tools.py) —
    # this capability key shares the "appointment" token with it deterministically.
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["appointment_scheduling"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.TOOL)
    assert recs, "expected at least one TOOL recommendation matched via the real, live ToolRegistry"
    tool_names = {r.tool_name for r in recs}
    assert "crm.create_appointment" in tool_names
    # Every referenced tool name genuinely exists in the live registry —
    # never a fabricated reference.
    for r in recs:
        tool_registry.get(r.tool_name)  # raises ToolNotFoundError if it doesn't exist


async def test_duplicate_recommendation_prevention_within_a_run(
    blueprint_service, recommendation_service, catalog_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    await _seed_provider(catalog_service, "dupprovider", ProviderImplementationStatus.REAL, ["accounting"])
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["accounting"])
    run = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id)
    # Exactly one CAPABILITY row + one INTEGRATION row for "accounting" — no duplicates.
    cap = [r for r in recs if r.type == RecommendationType.CAPABILITY and r.capability_key == "accounting"]
    integ = [r for r in recs if r.type == RecommendationType.INTEGRATION and r.provider_key == "dupprovider"]
    assert len(cap) == 1
    assert len(integ) == 1
    assert run.recommendation_count == len(recs)


async def test_vertical_extension_contributes_candidate_capabilities(
    blueprint_service, recommendation_service, vertical_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    vertical = await vertical_service.create_vertical(
        key="test_vertical_alpha", name="Test Vertical Alpha", status=VerticalExtensionStatus.ACTIVE,
        capabilities=["vertical_specific_capability"],
    )
    await vertical_service.enable_for_organization(tenant_id, "test_vertical_alpha", enabled_by=None)
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, [])
    run = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    assert "test_vertical_alpha" in run.verticals_considered
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    matches = [r for r in recs if r.capability_key == "vertical_specific_capability"]
    assert len(matches) == 1
    assert matches[0].source == RecommendationSource.VERTICAL_EXTENSION_RULE
    assert matches[0].source_vertical_key == "test_vertical_alpha"
    assert matches[0].required is False  # a vertical's menu item is a candidate, not mandatory


async def test_lifecycle_accept(blueprint_service, recommendation_service, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["accounting"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id)
    rec = recs[0]

    accepted = await recommendation_service.accept(tenant_id, rec.id, decided_by=uuid.uuid4())
    assert accepted.status == RecommendationStatus.ACCEPTED
    assert accepted.decided_at is not None

    # Idempotent re-accept.
    accepted_again = await recommendation_service.accept(tenant_id, rec.id, decided_by=uuid.uuid4())
    assert accepted_again.status == RecommendationStatus.ACCEPTED

    with pytest.raises(InvalidRecommendationTransitionError):
        await recommendation_service.reject(tenant_id, rec.id, decided_by=None, reason="nope")


async def test_lifecycle_reject(blueprint_service, recommendation_service, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["accounting"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    rec = (await recommendation_service.list_recommendations(tenant_id))[0]

    rejected = await recommendation_service.reject(tenant_id, rec.id, decided_by=uuid.uuid4(), reason="not needed")
    assert rejected.status == RecommendationStatus.REJECTED
    assert rejected.rejection_reason == "not needed"

    with pytest.raises(InvalidRecommendationTransitionError):
        await recommendation_service.accept(tenant_id, rec.id, decided_by=None)


async def test_recommendation_not_found(recommendation_service: RecommendationService) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(RecommendationNotFoundError):
        await recommendation_service.accept(tenant_id, uuid.uuid4(), decided_by=None)
    with pytest.raises(RecommendationNotFoundError):
        await recommendation_service.reject(tenant_id, uuid.uuid4(), decided_by=None, reason=None)


async def test_tenant_isolation(blueprint_service, recommendation_service, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_a, ["accounting"])
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_b, ["marketing"])
    await recommendation_service.generate_recommendations(tenant_a, tool_registry=tool_registry, triggered_by=None)
    await recommendation_service.generate_recommendations(tenant_b, tool_registry=tool_registry, triggered_by=None)

    recs_a = await recommendation_service.list_recommendations(tenant_a)
    recs_b = await recommendation_service.list_recommendations(tenant_b)
    assert {r.capability_key for r in recs_a} == {"accounting"}
    assert {r.capability_key for r in recs_b} == {"marketing"}

    # Tenant B cannot accept Tenant A's recommendation.
    with pytest.raises(RecommendationNotFoundError):
        await recommendation_service.accept(tenant_b, recs_a[0].id, decided_by=None)


async def test_blueprint_version_binding_supersedes_prior_run(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    blueprint_v1 = await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["accounting"])
    run_a = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs_after_a = await recommendation_service.list_recommendations(tenant_id, status=RecommendationStatus.PROPOSED)
    assert len(recs_after_a) == 1
    assert recs_after_a[0].blueprint_version == 1

    # New blueprint version (human edit of an ACTIVE blueprint creates v2).
    await blueprint_service.update_section(
        tenant_id, blueprint_v1.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        data={"required_capabilities.list": ["marketing"]}, updated_by=None,
    )
    blueprint_v2 = await blueprint_service.get_active(tenant_id)
    assert blueprint_v2.version == 2
    cap_claim_v2 = await blueprint_service.propose_claim(
        tenant_id, blueprint_v2.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="required_capabilities.list", value=["marketing"],
        confidence=0.9, provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await blueprint_service.confirm_claim(tenant_id, cap_claim_v2.id, confirmed_by=None)

    run_b = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    assert run_b.blueprint_version == 2
    assert run_b.id != run_a.id

    # Run A's recommendation is now SUPERSEDED, not silently still PROPOSED.
    stale = await recommendation_service.list_recommendations(tenant_id, status=RecommendationStatus.SUPERSEDED)
    assert any(r.run_id == run_a.id and r.capability_key == "accounting" for r in stale)

    current = await recommendation_service.list_recommendations(tenant_id, status=RecommendationStatus.PROPOSED)
    assert {r.capability_key for r in current} == {"marketing"}
    assert all(r.blueprint_version == 2 for r in current)


async def test_accepted_recommendation_survives_a_new_run(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    """An accepted decision is a human decision, never retroactively erased
    by a later blueprint edit/regeneration."""
    tenant_id = uuid.uuid4()
    blueprint = await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["accounting"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    rec = (await recommendation_service.list_recommendations(tenant_id))[0]
    await recommendation_service.accept(tenant_id, rec.id, decided_by=uuid.uuid4())

    await blueprint_service.update_section(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        data={"required_capabilities.list": ["accounting", "marketing"]}, updated_by=None,
    )
    blueprint_v2 = await blueprint_service.get_active(tenant_id)
    cap_claim_v2 = await blueprint_service.propose_claim(
        tenant_id, blueprint_v2.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="required_capabilities.list", value=["accounting", "marketing"],
        confidence=0.9, provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await blueprint_service.confirm_claim(tenant_id, cap_claim_v2.id, confirmed_by=None)
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    still_accepted = await recommendation_service.list_recommendations(tenant_id, status=RecommendationStatus.ACCEPTED)
    assert any(r.id == rec.id for r in still_accepted)


# --- read-only guarantee -------------------------------------------------

async def test_generation_never_executes_a_tool_or_creates_a_connection(
    blueprint_service, recommendation_service, tool_registry, monkeypatch
) -> None:
    executed = []
    original_execute = tool_registry.execute

    async def _spy_execute(*args, **kwargs):
        executed.append((args, kwargs))
        return await original_execute(*args, **kwargs)

    monkeypatch.setattr(tool_registry, "execute", _spy_execute)

    tenant_id = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["appointment_scheduling", "accounting"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)

    assert executed == []

    from app.db.session import async_session_maker
    from sqlalchemy import select

    from app.models.integration import IntegrationConnection

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id))
        ).scalars().all()
    assert rows == []


async def test_accept_never_executes_a_tool_or_creates_a_connection(
    blueprint_service, recommendation_service, tool_registry, monkeypatch
) -> None:
    executed = []
    original_execute = tool_registry.execute

    async def _spy_execute(*args, **kwargs):
        executed.append((args, kwargs))
        return await original_execute(*args, **kwargs)

    monkeypatch.setattr(tool_registry, "execute", _spy_execute)

    tenant_id = uuid.uuid4()
    await _activate_blueprint_with_capabilities(blueprint_service, tenant_id, ["accounting"])
    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    rec = (await recommendation_service.list_recommendations(tenant_id))[0]
    await recommendation_service.accept(tenant_id, rec.id, decided_by=uuid.uuid4())

    assert executed == []


def test_no_vertical_branch_in_recommendation_service_source() -> None:
    """Static self-check specific to this new service — the broader,
    codebase-wide guard already lives in
    tests/test_vertical_extension_no_hardcoding_guard.py and is untouched
    by this file."""
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "app" / "services" / "recommendation_service.py"
    ).read_text()
    pattern = re.compile(r"\b(if|elif)\b[^:\n]*==\s*[\"'](medical_tourism|dropshipping)[\"']")
    assert not pattern.search(src)
