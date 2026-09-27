"""Phase 2 cross-vertical validation (this task's explicit requirement):
proves the SAME Discovery -> Requirements -> Blueprint engine represents
both a medical-tourism-shaped business (international patients, provider-
partner referrals, commission revenue, social-media acquisition) and a
dropshipping-shaped business (supplier-based ecommerce, catalog/inventory/
orders/fulfillment/payments/marketing/support) using only generic
BlueprintSection/BlueprintClaim data plus an optional Phase 1
VerticalExtension registry reference — with ZERO
`if business_type == "medical_tourism"` / `if ... == "dropshipping"`
branches anywhere in app/ (see
tests/test_vertical_extension_no_hardcoding_guard.py, which this test
does not touch/weaken — it proves the DATA-level claim, not the code-guard
claim, which is asserted separately).

This test does NOT create any Hospital/Doctor/Procedure/Referral or
Product/SKU/Inventory/Supplier/Order table — those remain explicitly out
of scope. It proves the generic BlueprintSection JSONB + BlueprintClaim
model can hold each business's shape-appropriate data, not that
vertical-specific relational tables exist.
"""

import uuid

import pytest

from app.models.business_blueprint import (
    MINIMUM_BAR_SECTIONS,
    BlueprintSectionKey,
    BlueprintSectionStatus,
    BlueprintStatus,
    ClaimProvenance,
    ClaimType,
)
from app.services.business_blueprint_service import BusinessBlueprintService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tool_registry) -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


# Each scenario is a (section_key, claim_key, claim_value, claim_type) list
# — genuinely business-shaped data with no code path that inspects "which
# vertical is this" to decide behavior. The BusinessBlueprintService code
# exercised below is identical for both scenarios.

_MEDICAL_TOURISM_CLAIMS = [
    (BlueprintSectionKey.IDENTITY, "identity.description",
     "A referral brokerage connecting international patients with partner hospitals abroad.", ClaimType.FACT),
    (BlueprintSectionKey.INDUSTRY, "industry.name", "Medical tourism / cross-border healthcare referral", ClaimType.FACT),
    (BlueprintSectionKey.BUSINESS_MODEL, "business_model.type", "referral_brokerage", ClaimType.INFERENCE),
    (BlueprintSectionKey.CUSTOMERS, "customers.segment", "International patients seeking affordable elective procedures", ClaimType.FACT),
    (BlueprintSectionKey.SUPPLIERS_PROVIDERS, "providers.partners", ["Hospital Group A (Turkey)", "Hospital Group B (India)"], ClaimType.FACT),
    (BlueprintSectionKey.GEOGRAPHY, "geography.target_countries", ["TR", "IN"], ClaimType.FACT),
    (BlueprintSectionKey.CHANNELS, "channels.acquisition", ["instagram", "facebook_ads", "referral"], ClaimType.FACT),
    (BlueprintSectionKey.REVENUE, "revenue.model", "referral_commission_percentage", ClaimType.INFERENCE),
    (BlueprintSectionKey.COMPLIANCE, "compliance.licensing", None, ClaimType.UNKNOWN),
    (BlueprintSectionKey.REQUIRED_CAPABILITIES, "required_capabilities.list",
     ["multi_currency_commission", "patient_lead_pipeline", "provider_directory"], ClaimType.REQUIREMENT),
]

_DROPSHIPPING_CLAIMS = [
    (BlueprintSectionKey.IDENTITY, "identity.description",
     "A supplier-based ecommerce storefront selling home goods with no owned inventory.", ClaimType.FACT),
    (BlueprintSectionKey.INDUSTRY, "industry.name", "Ecommerce / dropshipping retail", ClaimType.FACT),
    (BlueprintSectionKey.BUSINESS_MODEL, "business_model.type", "dropship_retail", ClaimType.INFERENCE),
    (BlueprintSectionKey.CUSTOMERS, "customers.segment", "Direct-to-consumer online shoppers", ClaimType.FACT),
    (BlueprintSectionKey.SUPPLIERS_PROVIDERS, "suppliers.list", ["AliExpress Supplier X", "CJ Dropshipping"], ClaimType.FACT),
    (BlueprintSectionKey.GEOGRAPHY, "geography.target_countries", ["US", "CA"], ClaimType.FACT),
    (BlueprintSectionKey.CHANNELS, "channels.acquisition", ["tiktok_ads", "meta_ads", "email"], ClaimType.FACT),
    (BlueprintSectionKey.REVENUE, "revenue.model", "product_margin", ClaimType.INFERENCE),
    (BlueprintSectionKey.OPERATIONS, "operations.fulfillment", "supplier_direct_ship", ClaimType.FACT),
    (BlueprintSectionKey.REQUIRED_CAPABILITIES, "required_capabilities.list",
     ["catalog_sync", "order_routing_to_supplier", "payment_processing"], ClaimType.REQUIREMENT),
]


async def _run_scenario(service: BusinessBlueprintService, claims: list) -> tuple:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)

    for section_key, key, value, claim_type in claims:
        claim = await service.propose_claim(
            tenant_id, blueprint.id, section_key=section_key.value, claim_type=claim_type.value,
            key=key, value=value, confidence=(0.7 if claim_type == ClaimType.INFERENCE else None),
            provenance=(
                ClaimProvenance.AI_INFERRED.value
                if claim_type in (ClaimType.INFERENCE, ClaimType.REQUIREMENT)
                else ClaimProvenance.USER_STATED.value
            ),
            discovery_turn_id=None, evidence_ref=None,
        )
        if claim_type != ClaimType.UNKNOWN:
            await service.confirm_claim(tenant_id, claim.id, confirmed_by=uuid.uuid4())

    activated = await service.activate(tenant_id, blueprint.id, activated_by=uuid.uuid4())
    sections = await service.list_sections(tenant_id, activated.id)
    return tenant_id, activated, sections


async def test_medical_tourism_shaped_business_activates(service: BusinessBlueprintService) -> None:
    tenant_id, blueprint, sections = await _run_scenario(service, _MEDICAL_TOURISM_CLAIMS)
    assert blueprint.status == BlueprintStatus.ACTIVE

    by_key = {s.section_key: s for s in sections}
    assert by_key[BlueprintSectionKey.SUPPLIERS_PROVIDERS.value].data["providers.partners"] == [
        "Hospital Group A (Turkey)", "Hospital Group B (India)"
    ]
    assert by_key[BlueprintSectionKey.REQUIRED_CAPABILITIES.value].status == BlueprintSectionStatus.COMPLETE
    # The compliance Unknown never silently defaulted — it stays proposed,
    # non-confirmed, and the COMPLIANCE section is correctly NOT complete.
    assert by_key[BlueprintSectionKey.COMPLIANCE.value].status != BlueprintSectionStatus.COMPLETE
    claims = await service.list_claims(tenant_id, blueprint.id)
    unknowns = [c for c in claims if c.claim_type == ClaimType.UNKNOWN.value]
    assert len(unknowns) == 1
    assert unknowns[0].status == "PROPOSED"  # never auto-confirmed


async def test_dropshipping_shaped_business_activates(service: BusinessBlueprintService) -> None:
    tenant_id, blueprint, sections = await _run_scenario(service, _DROPSHIPPING_CLAIMS)
    assert blueprint.status == BlueprintStatus.ACTIVE

    by_key = {s.section_key: s for s in sections}
    assert by_key[BlueprintSectionKey.SUPPLIERS_PROVIDERS.value].data["suppliers.list"] == [
        "AliExpress Supplier X", "CJ Dropshipping"
    ]
    assert by_key[BlueprintSectionKey.OPERATIONS.value].data["operations.fulfillment"] == "supplier_direct_ship"
    assert by_key[BlueprintSectionKey.REQUIRED_CAPABILITIES.value].status == BlueprintSectionStatus.COMPLETE


async def test_both_scenarios_use_the_identical_generic_schema(service: BusinessBlueprintService) -> None:
    """The literal proof this is one engine, not two: both blueprints use
    the same fixed BlueprintSectionKey enum, the same BusinessBlueprintService
    methods, and the same ClaimType vocabulary — only the JSONB *values*
    differ, never the code path."""
    tenant_a, blueprint_a, sections_a = await _run_scenario(service, _MEDICAL_TOURISM_CLAIMS)
    tenant_b, blueprint_b, sections_b = await _run_scenario(service, _DROPSHIPPING_CLAIMS)

    assert {s.section_key for s in sections_a} == {s.section_key for s in sections_b} == {
        k.value for k in BlueprintSectionKey
    }
    assert blueprint_a.tenant_id != blueprint_b.tenant_id
    minimum_bar_keys = {k.value for k in MINIMUM_BAR_SECTIONS}
    for sections in (sections_a, sections_b):
        by_key = {s.section_key: s for s in sections}
        for key in minimum_bar_keys:
            assert by_key[key].status == BlueprintSectionStatus.COMPLETE


def test_no_vertical_branch_in_business_blueprint_service_source() -> None:
    """Static self-check specific to this new service (the broader,
    codebase-wide guard already lives in
    tests/test_vertical_extension_no_hardcoding_guard.py and is untouched
    by this file)."""
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "app" / "services" / "business_blueprint_service.py"
    ).read_text()
    pattern = re.compile(r"\b(if|elif)\b[^:\n]*==\s*[\"'](medical_tourism|dropshipping)[\"']")
    assert not pattern.search(src)
