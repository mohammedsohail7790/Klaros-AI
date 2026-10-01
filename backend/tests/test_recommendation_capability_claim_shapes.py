"""Bug #2 (KLAROS final platform completion task): the Recommendation
Engine's `_collect_capability_requirements` previously only read a
REQUIRED_CAPABILITIES `BlueprintClaim`'s `.value` as a plain string or a
list of strings ("value-shaped"). A valid AI-generated structured claim
can instead be "key-shaped" — `claim.key` IS the capability name and
`claim.value` is the literal boolean `True` (one claim per stated
capability, e.g. `{"key": "telemedicine", "value": true}`) — and that
shape was silently dropped, so BASELINE_RULE recommendations derived from
the business's own stated requirements could go missing.

This file exercises `RecommendationService._collect_capability_requirements`
(via `generate_recommendations`) directly against both claim shapes, plus
the malformed/unrelated/duplicate/tenant-isolation/provenance edge cases
called out in the task brief. It deliberately builds claims with
`blueprint_service.propose_claim` directly (bypassing the existing
`_activate_blueprint_with_capabilities` test helper in
test_recommendation_service.py, which only ever produces value-shaped
list claims) so each shape under test is explicit and unambiguous.
"""

import uuid

import pytest

from app.models.business_blueprint import (
    MINIMUM_BAR_SECTIONS,
    BlueprintSectionKey,
    ClaimProvenance,
    ClaimType,
)
from app.models.recommendation import RecommendationSource, RecommendationStatus, RecommendationType
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.recommendation_service import RecommendationService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def blueprint_service(tool_registry) -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


@pytest.fixture
def recommendation_service(tool_registry) -> RecommendationService:
    from app.db.session import async_session_maker

    return RecommendationService(async_session_maker)


async def _fill_non_capability_minimum_bar(blueprint_service: BusinessBlueprintService, tenant_id: uuid.UUID):
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
    return blueprint


async def _propose_and_confirm(
    blueprint_service: BusinessBlueprintService,
    tenant_id: uuid.UUID,
    blueprint_id: uuid.UUID,
    *,
    key: str,
    value,
    confidence: float | None = 0.9,
    provenance: str = ClaimProvenance.AI_INFERRED.value,
):
    claim = await blueprint_service.propose_claim(
        tenant_id, blueprint_id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key=key, value=value,
        confidence=confidence, provenance=provenance, discovery_turn_id=None, evidence_ref=None,
    )
    return await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)


async def test_a_value_shaped_capability_claim_still_works(blueprint_service, recommendation_service, tool_registry) -> None:
    """(A) Backward compatibility: list-of-strings value-shaped claim."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="required_capabilities.list", value=["telemedicine"])
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert {r.capability_key for r in recs} == {"telemedicine"}
    assert recs[0].source == RecommendationSource.BASELINE_RULE
    assert recs[0].required is True


async def test_b_key_shaped_capability_claim_is_no_longer_dropped(blueprint_service, recommendation_service, tool_registry) -> None:
    """(B) The bug: a key-shaped claim {"key": "telemedicine", "value": true}
    must now produce a CAPABILITY recommendation instead of being silently
    discarded."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="telemedicine", value=True)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    run = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    assert run.recommendation_count >= 1
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert {r.capability_key for r in recs} == {"telemedicine"}
    rec = recs[0]
    assert rec.required is True
    assert rec.source == RecommendationSource.BASELINE_RULE
    assert rec.based_on and rec.based_on[0]["kind"] == "blueprint_claim"


async def test_c_malformed_claims_are_skipped_not_erroring(blueprint_service, recommendation_service, tool_registry) -> None:
    """(C) Malformed shapes (non-boolean-true scalar value, empty string,
    None, a bare dict) never crash generation and never produce a spurious
    capability."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="weird_number", value=42)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="weird_empty", value="")
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="weird_dict", value={"nested": "thing"})
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="unknown_claim", value=None)
    # A genuinely valid claim alongside the malformed ones, to prove
    # malformed siblings don't poison the whole batch.
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="valid_one", value=True)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert {r.capability_key for r in recs} == {"valid_one"}


async def test_d_unrelated_key_value_claim_is_not_promoted(blueprint_service, recommendation_service, tool_registry) -> None:
    """(D) A claim whose value is exactly boolean False must never be
    interpreted as "the capability named by its key is required" (or
    required=False) — it must simply not contribute a capability at all,
    proving the fix distinguishes "stated true" from anything else."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="explicitly_not_needed", value=False)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert recs == []


async def test_e_duplicate_capability_across_shapes_is_merged_once(blueprint_service, recommendation_service, tool_registry) -> None:
    """(E) The same capability stated once value-shaped and once
    key-shaped merges into a single recommendation, with evidence from
    both claims preserved."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="required_capabilities.list", value=["telemedicine"])
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="telemedicine", value=True)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert len(recs) == 1
    assert recs[0].capability_key == "telemedicine"
    assert len(recs[0].based_on) == 2


async def test_f_cross_tenant_isolation_for_key_shaped_claims(blueprint_service, recommendation_service, tool_registry) -> None:
    """(F) A key-shaped capability claim for tenant A must never leak into
    tenant B's recommendations."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    blueprint_a = await _fill_non_capability_minimum_bar(blueprint_service, tenant_a)
    await _propose_and_confirm(blueprint_service, tenant_a, blueprint_a.id, key="telemedicine", value=True)
    await blueprint_service.activate(tenant_a, blueprint_a.id, activated_by=None)

    blueprint_b = await _fill_non_capability_minimum_bar(blueprint_service, tenant_b)
    await _propose_and_confirm(blueprint_service, tenant_b, blueprint_b.id, key="payment_processing", value=True)
    await blueprint_service.activate(tenant_b, blueprint_b.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_a, tool_registry=tool_registry, triggered_by=None)
    await recommendation_service.generate_recommendations(tenant_b, tool_registry=tool_registry, triggered_by=None)

    recs_a = await recommendation_service.list_recommendations(tenant_a, type_=RecommendationType.CAPABILITY)
    recs_b = await recommendation_service.list_recommendations(tenant_b, type_=RecommendationType.CAPABILITY)
    assert {r.capability_key for r in recs_a} == {"telemedicine"}
    assert {r.capability_key for r in recs_b} == {"payment_processing"}


async def test_g_recommendation_generation_end_to_end_for_key_shaped_claim(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    """(G) A full generation run persists a proper RecommendationRun +
    Recommendation row set (not just an in-memory requirement) for a
    key-shaped claim."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(blueprint_service, tenant_id, blueprint.id, key="telemedicine", value=True)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    run = await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    fetched_run = await recommendation_service.get_run(tenant_id, run.id)
    assert fetched_run.recommendation_count == run.recommendation_count
    assert fetched_run.recommendation_count >= 1
    recs = await recommendation_service.list_recommendations(tenant_id, status=RecommendationStatus.PROPOSED)
    assert any(r.capability_key == "telemedicine" for r in recs)


async def test_h_provenance_preserved_for_key_shaped_claim(blueprint_service, recommendation_service, tool_registry) -> None:
    """(H) The claim's own confidence and the claim id evidence pointer
    (provenance) are preserved exactly as they are for the value-shaped
    path — the fix must not weaken the existing evidence trail."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    claim = await blueprint_service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="telemedicine", value=True,
        confidence=0.77, provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert len(recs) == 1
    assert recs[0].confidence == pytest.approx(0.77)
    assert recs[0].based_on[0]["claim_id"] == str(claim.id)
    assert recs[0].based_on[0]["section_key"] == BlueprintSectionKey.REQUIRED_CAPABILITIES.value


async def test_i_existing_value_shaped_multi_capability_list_unaffected(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    """(I)/(J) Non-regression: an existing-shape, multi-capability list
    claim (the shape every pre-existing Medical Tourism / generic-vertical
    blueprint uses, per test_recommendation_service.py's own
    `_activate_blueprint_with_capabilities` helper) still yields one
    recommendation per capability, unaffected by the new key-shaped path."""
    tenant_id = uuid.uuid4()
    blueprint = await _fill_non_capability_minimum_bar(blueprint_service, tenant_id)
    await _propose_and_confirm(
        blueprint_service, tenant_id, blueprint.id,
        key="required_capabilities.list", value=["patient_lead_intake", "provider_directory", "referral_commission"],
    )
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    await recommendation_service.generate_recommendations(tenant_id, tool_registry=tool_registry, triggered_by=None)
    recs = await recommendation_service.list_recommendations(tenant_id, type_=RecommendationType.CAPABILITY)
    assert {r.capability_key for r in recs} == {"patient_lead_intake", "provider_directory", "referral_commission"}
    assert all(r.source == RecommendationSource.BASELINE_RULE and r.required is True for r in recs)
