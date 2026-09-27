"""Phase 3 cross-vertical validation (this phase's explicit requirement):
proves the SAME RecommendationService produces recommendations for both a
medical-tourism-shaped Business Blueprint and a dropshipping-shaped
Business Blueprint using only generic BlueprintClaim/VerticalExtension
registry data, with ZERO `if business_type == "medical_tourism"` /
`if ... == "dropshipping"` branches anywhere in
app/services/recommendation_service.py (see
tests/test_vertical_extension_no_hardcoding_guard.py, which this test
does not touch/weaken — it proves the DATA-level claim, not the
codebase-wide code-guard claim, which is asserted separately there and
again, narrowly, at the bottom of this file).

Mirrors tests/test_cross_vertical_blueprint_validation.py's structure and
its exact medical-tourism / dropshipping capability vocabulary (generic
capability-level recommendations only — no Hospital/Clinic/Doctor/
Procedure or Product/SKU/Inventory/Supplier/Order table, matching this
phase's explicit scope boundary).
"""

import uuid

import pytest

from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, BlueprintSectionKey, ClaimProvenance, ClaimType
from app.models.recommendation import RecommendationType
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


# Generic capability-level requirements only (never a vertical-specific
# table) — matching this task's explicit list of permitted capability
# concepts: "provider management, lead management, appointment scheduling,
# communications, marketing, analytics, document handling, payments,
# referral tracking for medical-tourism-shaped" and "catalog management,
# customer management, order management, inventory, supplier coordination,
# fulfillment, payments, marketing, analytics for dropshipping-shaped".
_MEDICAL_TOURISM_CAPABILITIES = [
    "lead_management",
    "appointment_scheduling",
    "marketing",
    "referral_tracking",
    "payment_processing",
]

_DROPSHIPPING_CAPABILITIES = [
    "customer_management",
    "order_management",
    "marketing",
    "payment_processing",
]


async def _run_scenario(
    blueprint_service: BusinessBlueprintService, recommendation_service: RecommendationService,
    tool_registry, capability_keys: list[str],
):
    tenant_id = uuid.uuid4()
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
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    run = await recommendation_service.generate_recommendations(
        tenant_id, tool_registry=tool_registry, triggered_by=None
    )
    recs = await recommendation_service.list_recommendations(tenant_id)
    return tenant_id, run, recs


async def test_medical_tourism_shaped_business_produces_recommendations(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    tenant_id, run, recs = await _run_scenario(
        blueprint_service, recommendation_service, tool_registry, _MEDICAL_TOURISM_CAPABILITIES
    )
    cap_recs = {r.capability_key for r in recs if r.type == RecommendationType.CAPABILITY}
    assert cap_recs == set(_MEDICAL_TOURISM_CAPABILITIES)
    assert run.recommendation_count == len(recs)
    assert all(r.blueprint_version == 1 for r in recs)


async def test_dropshipping_shaped_business_produces_recommendations(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    tenant_id, run, recs = await _run_scenario(
        blueprint_service, recommendation_service, tool_registry, _DROPSHIPPING_CAPABILITIES
    )
    cap_recs = {r.capability_key for r in recs if r.type == RecommendationType.CAPABILITY}
    assert cap_recs == set(_DROPSHIPPING_CAPABILITIES)


async def test_both_scenarios_use_the_identical_engine_code_path(
    blueprint_service, recommendation_service, tool_registry
) -> None:
    """The literal proof this is one engine, not two: both runs use the
    exact same RecommendationService.generate_recommendations method, the
    same Recommendation/RecommendationRun schema, and the same
    RecommendationType/RecommendationSource vocabulary — only the
    capability-key *data* differs, never the code path."""
    tenant_a, run_a, recs_a = await _run_scenario(
        blueprint_service, recommendation_service, tool_registry, _MEDICAL_TOURISM_CAPABILITIES
    )
    tenant_b, run_b, recs_b = await _run_scenario(
        blueprint_service, recommendation_service, tool_registry, _DROPSHIPPING_CAPABILITIES
    )
    assert tenant_a != tenant_b
    assert run_a.id != run_b.id
    for recs in (recs_a, recs_b):
        for r in recs:
            assert r.type in {t.value for t in RecommendationType}
    # Shared "marketing" and "payment_processing" capabilities across both
    # verticals resolve through the identical matching logic.
    shared = {"marketing", "payment_processing"}
    keys_a = {r.capability_key for r in recs_a if r.type == RecommendationType.CAPABILITY}
    keys_b = {r.capability_key for r in recs_b if r.type == RecommendationType.CAPABILITY}
    assert shared <= keys_a
    assert shared <= keys_b


def test_no_vertical_branch_in_recommendation_engine_source() -> None:
    """Narrow, file-specific self-check (the codebase-wide guard lives in
    tests/test_vertical_extension_no_hardcoding_guard.py and is untouched
    by this file) — scans every file this phase added/touched."""
    import re
    from pathlib import Path

    app_root = Path(__file__).resolve().parent.parent / "app"
    pattern = re.compile(r"\b(if|elif)\b[^:\n]*==\s*[\"'](medical_tourism|dropshipping)[\"']")
    phase_3_files = [
        app_root / "services" / "recommendation_service.py",
        app_root / "models" / "recommendation.py",
        app_root / "api" / "v1" / "recommendations.py",
        app_root / "api" / "tool_deps_recommendations.py",
    ]
    violations = []
    for path in phase_3_files:
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            violations.append(f"{path}:{line_no}: {match.group(0)!r}")
    assert not violations, violations
