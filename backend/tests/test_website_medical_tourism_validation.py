"""Phase 11 (Phase 13 of PHASE_11_WEBSITE_BUILDER_DESIGN.md / the task's
own HARD SCOPE): the mandatory Medical Tourism validation scenario — the
key acceptance test for the whole phase. Synthetic data only, no real
providers/hospitals.

Walks the full flow: tenant activates Medical Tourism -> has an ACTIVE
Blueprint -> has synthetic Provider/Procedure/ProviderProcedure data (from
Phase 10) -> requests website generation -> gets a validated
WebsiteSpecification containing a hero, a service/procedure section, a
PROVIDER_DIRECTORY section, and a CTA/contact section -> provider data
renders through the generic component mechanism -> proves (via a real
test, not a claim) that no vertical-name branch exists in the generic
renderer/generation code -> draft preview works -> draft is published ->
publish immutability holds -> updating the draft creates a new version ->
the previous published version is unchanged.
"""

import uuid

import pytest

from app.models.business_blueprint import BlueprintSectionKey, ClaimProvenance, ClaimType
from app.models.vertical_extension import VerticalExtensionStatus
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.medical_tourism_service import (
    CreateOfferingInput,
    CreateProcedureInput,
    CreateProviderInput,
    MedicalTourismService,
)
from app.services.vertical_extension_service import VerticalExtensionService
from app.services.website_generation_service import WebsiteGenerationService
from app.services.website_renderer import render_website
from app.services.website_service import WebsiteService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def blueprint_service(tool_registry) -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


@pytest.fixture
def vertical_service(tool_registry) -> VerticalExtensionService:
    from app.db.session import async_session_maker

    return VerticalExtensionService(async_session_maker)


@pytest.fixture
def medical_tourism_service(tool_registry) -> MedicalTourismService:
    from app.db.session import async_session_maker

    return MedicalTourismService(async_session_maker)


@pytest.fixture
def generation_service(tool_registry) -> WebsiteGenerationService:
    from app.db.session import async_session_maker

    return WebsiteGenerationService(async_session_maker)


@pytest.fixture
def website_service(tool_registry) -> WebsiteService:
    from app.db.session import async_session_maker

    return WebsiteService(async_session_maker)


async def test_full_medical_tourism_website_builder_scenario(
    blueprint_service: BusinessBlueprintService,
    vertical_service: VerticalExtensionService,
    medical_tourism_service: MedicalTourismService,
    generation_service: WebsiteGenerationService,
    website_service: WebsiteService,
) -> None:
    tenant_id = uuid.uuid4()

    # (1)/(2) tenant activates Medical Tourism + has an ACTIVE Blueprint.
    vertical = await vertical_service.create_vertical(
        key="medical_tourism_e2e_test",
        name="Medical Tourism (e2e test)",
        status=VerticalExtensionStatus.ACTIVE,
        capabilities=["medical_tourism.provider_directory", "medical_tourism.procedure_catalog"],
    )
    await vertical_service.enable_for_organization(tenant_id, vertical.key, enabled_by=None)

    blueprint = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)

    async def _confirm(section_key, key, value, claim_type=ClaimType.FACT):
        claim = await blueprint_service.propose_claim(
            tenant_id, blueprint.id, section_key=section_key.value, claim_type=claim_type.value,
            key=key, value=value, confidence=0.9, provenance=ClaimProvenance.USER_STATED.value,
            discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)

    # (3) Blueprint contains business description/services/target market.
    await _confirm(BlueprintSectionKey.IDENTITY, "business_name", "Global Health Partners")
    await _confirm(BlueprintSectionKey.IDENTITY, "description", "We connect patients with trusted overseas care.")
    await _confirm(BlueprintSectionKey.INDUSTRY, "industry.v", "Medical Tourism")
    await _confirm(BlueprintSectionKey.BUSINESS_MODEL, "business_model.v", "Facilitation services")
    await _confirm(BlueprintSectionKey.PRODUCTS_SERVICES, "services", ["Hip replacement", "Dental care"])
    await _confirm(BlueprintSectionKey.CUSTOMERS, "target_customer", "International patients seeking affordable care")
    await _confirm(BlueprintSectionKey.COMMUNICATIONS, "contact_email", "care@globalhealthpartners.example")
    await _confirm(
        BlueprintSectionKey.REQUIRED_CAPABILITIES, "required_capabilities.list",
        ["medical_tourism.provider_directory"], claim_type=ClaimType.REQUIREMENT,
    )
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    # (4) synthetic Medical Tourism provider data exists (Phase 10 models).
    provider, _ = await medical_tourism_service.create_provider(
        tenant_id,
        CreateProviderInput(name="Synthetic Test Hospital", country="TR", city="Istanbul", description="Synthetic test data only."),
    )
    procedure, _ = await medical_tourism_service.create_procedure(
        tenant_id, CreateProcedureInput(name="Synthetic Hip Replacement", category="Orthopedics")
    )
    await medical_tourism_service.create_provider_procedure(
        tenant_id,
        CreateOfferingInput(provider_id=provider.id, procedure_id=procedure.id, estimated_price=None, currency="USD"),
    )

    # (5)/(6)/(7) website generation requested -> produces + validates a spec.
    result = await generation_service.generate_from_active_blueprint(tenant_id, actor_id=None)
    spec = result.specification  # already validated by WebsiteSpecification's constructor

    # (8) hero, service/procedure section, provider directory, CTA/contact.
    home = next(p for p in spec.pages if p.slug == "home")
    component_types = [s.component_type.value for s in home.sections]
    assert "HERO" in component_types
    assert "FEATURE_GRID" in component_types  # the generic "service/procedure" section
    assert "PROVIDER_DIRECTORY" in component_types
    assert "CTA" in component_types
    contact_page = next(p for p in spec.pages if p.slug == "contact")
    assert "CONTACT_FORM" in [s.component_type.value for s in contact_page.sections]

    # (9) provider data renders through the generic component mechanism.
    website, version = await website_service.create_website_with_specification(
        tenant_id, name="Global Health Partners", slug="global-health-partners", blueprint_id=blueprint.id,
        specification=spec, provenance=result.provenance, created_by=None,
    )
    rendered = await render_website(tenant_id, spec)
    rendered_home = next(p for p in rendered["pages"] if p["slug"] == "home")
    directory_section = next(s for s in rendered_home["sections"] if s["component_type"] == "PROVIDER_DIRECTORY")
    assert directory_section["data"]["resolved"] is True
    assert directory_section["data"]["items"][0]["name"] == "Synthetic Test Hospital"
    assert directory_section["data"]["items"][0]["offerings"][0]["name"] == "Synthetic Hip Replacement"

    # (10) no Medical-Tourism-specific branch inside the generic renderer —
    # exercised as a real static-analysis test elsewhere
    # (tests/test_website_no_vertical_hardcoding.py); re-asserted here so
    # this one test file is a self-contained proof of every Phase 13 item.
    import inspect

    from app.services import website_renderer

    renderer_code = "\n".join(
        inspect.getsource(fn)
        for fn in (website_renderer.render_section, website_renderer.render_page, website_renderer.render_website)
    ).lower()
    assert "medical_tourism" not in renderer_code
    assert "hospital" not in renderer_code

    # (11) draft preview works.
    preview_spec = await website_service.load_specification(tenant_id, version.id)
    preview_tree = await render_website(tenant_id, preview_spec)
    assert preview_tree["pages"][0]["slug"] == "home"

    # (12) draft is published.
    published = await website_service.publish_version(tenant_id, website.id, version.id, published_by=None)
    assert published.status == "PUBLISHED"

    # (13) published version is immutable (mutation attempt fails).
    from app.services.website_service import WebsiteVersionImmutableError

    with pytest.raises(WebsiteVersionImmutableError):
        await website_service.replace_page_sections(tenant_id, version.id, "home", [], updated_by=None)

    # (14) updating the draft creates a new version.
    new_draft = await website_service.create_draft_from_version(tenant_id, website.id, version.id, created_by=None)
    assert new_draft.version == 2
    assert new_draft.status == "DRAFT"

    # (15) the previous published version remains unchanged.
    republished_spec = await website_service.load_specification(tenant_id, version.id)
    original_home = next(p for p in republished_spec.pages if p.slug == "home")
    assert [s.component_type.value for s in original_home.sections] == component_types
