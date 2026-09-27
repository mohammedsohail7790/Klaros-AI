"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md), Phase 13 mandate:
one complete vertical validation scenario — a tenant activates the
medical_tourism vertical, builds its provider/procedure/offering
directory, and the resulting data is genuinely reachable through the
Recommendation Engine, a governed ToolRegistry tool, and the existing
Agent Runtime, with zero new execution machinery and zero vertical-name
branching anywhere in this path. Synthetic test data only — this is a
test scenario, never a claim about real-world providers.

Also covers Phase 8 (Recommendation Engine integration: the vertical's
capabilities are picked up generically via VerticalExtension.capabilities
+ the Tool Catalog's keyword matcher, with no code change to
recommendation_service.py) and Phase 10/agent-compatibility (the existing
Agent Runtime executing a real, permitted medical_tourism tool through
the unmodified governance chain: AgentVersion snapshot permissions ->
ToolRegistry -> tenant-scoped execution -> audit).
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models.agent import AgentExecutionStatus
from app.models.audit_log import AuditLog
from app.models.business_blueprint import BlueprintSectionKey, ClaimProvenance, ClaimType, MINIMUM_BAR_SECTIONS
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_service import AgentService
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.medical_tourism_service import (
    CreateOfferingInput,
    CreateProcedureInput,
    CreateProviderInput,
    MedicalTourismService,
)
from app.services.recommendation_service import RecommendationService
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
def vertical_service(tool_registry) -> VerticalExtensionService:
    from app.db.session import async_session_maker

    return VerticalExtensionService(async_session_maker)


@pytest.fixture
def medical_tourism_service(tool_registry) -> MedicalTourismService:
    from app.db.session import async_session_maker

    return MedicalTourismService(async_session_maker)


@pytest.fixture
def agent_service(tool_registry) -> AgentService:
    from app.db.session import async_session_maker

    return AgentService(async_session_maker)


@pytest.fixture
def execution_service(tool_registry) -> AgentExecutionService:
    from app.db.session import async_session_maker

    return AgentExecutionService(async_session_maker, tool_registry)


async def _activate_blueprint_for_medical_tourism(
    blueprint_service: BusinessBlueprintService,
    vertical_service: VerticalExtensionService,
    tenant_id: uuid.UUID,
):
    """Step 1 of the target flow: Business Discovery -> Business
    Blueprint -> Vertical=medical_tourism. Registers/enables the
    medical_tourism VerticalExtension for this tenant (the exact
    OrganizationVerticalExtension join row KLAROS_DOMAIN_EXTENSIBILITY_
    SPEC.md's extension pattern calls for) and activates a minimum-bar
    Business Blueprint referencing it."""
    from app.data.vertical_extension_seed import SEED_VERTICALS

    medical_tourism_seed = next(v for v in SEED_VERTICALS if v["key"] == "medical_tourism")
    try:
        vertical = await vertical_service.get_by_key("medical_tourism")
    except Exception:
        vertical = await vertical_service.create_vertical(
            key="medical_tourism",
            name=medical_tourism_seed["name"],
            description=medical_tourism_seed["description"],
            version=medical_tourism_seed["version"],
            status=str(medical_tourism_seed["status"]),
            capabilities=medical_tourism_seed["capabilities"],
        )
    await vertical_service.enable_for_organization(tenant_id, vertical.key, enabled_by=None)

    blueprint = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)
    for key in MINIMUM_BAR_SECTIONS:
        if key == BlueprintSectionKey.REQUIRED_CAPABILITIES:
            continue
        claim = await blueprint_service.propose_claim(
            tenant_id, blueprint.id, section_key=key.value, claim_type=ClaimType.FACT.value,
            key=f"{key.value.lower()}.v", value="medical tourism business", confidence=None,
            provenance=ClaimProvenance.USER_STATED.value, discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)

    cap_claim = await blueprint_service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="required_capabilities.list",
        value=["medical_tourism.provider_directory"], confidence=0.9,
        provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await blueprint_service.confirm_claim(tenant_id, cap_claim.id, confirmed_by=None)

    return await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)


async def test_full_medical_tourism_vertical_validation_scenario(
    blueprint_service: BusinessBlueprintService,
    vertical_service: VerticalExtensionService,
    medical_tourism_service: MedicalTourismService,
    recommendation_service: RecommendationService,
    agent_service: AgentService,
    execution_service: AgentExecutionService,
    tool_registry,
) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # --- Step 1: activate medical_tourism vertical + Business Blueprint ---
    blueprint = await _activate_blueprint_for_medical_tourism(blueprint_service, vertical_service, tenant_a)
    assert await vertical_service.is_enabled_for_organization(tenant_a, "medical_tourism") is True

    # --- Step 2: configure target destination/country + create providers ---
    provider_tr, _ = await medical_tourism_service.create_provider(
        tenant_a, CreateProviderInput(name="Istanbul Health Hub", country="TR", city="Istanbul")
    )
    provider_in, _ = await medical_tourism_service.create_provider(
        tenant_a, CreateProviderInput(name="Delhi Care Institute", country="IN", city="Delhi")
    )

    # --- Step 3: create hospitals/clinics -- Provider already models this;
    # verify the directory now genuinely has two active entries. ---
    providers, total = await medical_tourism_service.list_providers(tenant_a)
    assert total == 2

    # --- Step 4: create procedures ---
    procedure, _ = await medical_tourism_service.create_procedure(
        tenant_a, CreateProcedureInput(name="Hip Replacement", category="Orthopedic")
    )

    # --- Step 5: create provider offerings + associate providers with
    # destinations (Provider.country/city IS the destination association,
    # per KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md §3's rejection of a separate
    # Destination table) ---
    offering_tr, _ = await medical_tourism_service.create_provider_procedure(
        tenant_a,
        CreateOfferingInput(provider_id=provider_tr.id, procedure_id=procedure.id, estimated_price=8000, currency="USD"),
    )
    offering_in, _ = await medical_tourism_service.create_provider_procedure(
        tenant_a,
        CreateOfferingInput(provider_id=provider_in.id, procedure_id=procedure.id, estimated_price=4500, currency="USD"),
    )

    # --- Step 6: query available providers/procedures ---
    offerings, offering_total = await medical_tourism_service.list_offerings(tenant_a, procedure_id=procedure.id)
    assert offering_total == 2
    assert {o.provider_id for o in offerings} == {provider_tr.id, provider_in.id}

    # --- Step 7: verify tenant isolation -- a second tenant sees none of this ---
    tenant_b_providers, tenant_b_total = await medical_tourism_service.list_providers(tenant_b)
    assert tenant_b_total == 0
    assert await vertical_service.is_enabled_for_organization(tenant_b, "medical_tourism") is False

    # --- Step 8: verify Recommendations can consume the relevant
    # capabilities -- generic engine, zero vertical-name branching, driven
    # entirely by VerticalExtension.capabilities + the Blueprint's own
    # confirmed REQUIRED_CAPABILITIES claim. ---
    run = await recommendation_service.generate_recommendations(
        tenant_a, tool_registry=tool_registry, triggered_by=None
    )
    recommendations = await recommendation_service.list_recommendations(tenant_a, blueprint_id=blueprint.id)
    capability_keys = {r.capability_key for r in recommendations}
    assert "medical_tourism.provider_directory" in capability_keys
    # Every one of the vertical's other registered capabilities (contributed
    # candidate, not required) must also have produced a CAPABILITY
    # recommendation -- proves the VerticalExtension.capabilities list is
    # genuinely read, not just the blueprint's own required list.
    assert "medical_tourism.cross_border_commission" in capability_keys
    # At least one TOOL recommendation should match a real medical_tourism
    # tool via the Tool Catalog's keyword overlap (medical_tourism.search_providers
    # matches "medical_tourism.provider_directory" by shared "provider"/
    # "directory"-shaped tokens in its description).
    tool_recs = [r for r in recommendations if r.type == "TOOL" and r.tool_name and "medical_tourism." in r.tool_name]
    assert len(tool_recs) >= 1, f"expected at least one medical_tourism TOOL recommendation, got: {recommendations}"

    # --- Step 9: verify a governed ToolRegistry operation can access the
    # domain (a plain, ungoverned direct call — the same "governed action"
    # proof retention_referrals/customers API tests already rely on for
    # their own domains). ---
    from app.tools.base import ExecutionContext
    from app.models.actor import ActorType

    ctx = ExecutionContext(tenant_id=tenant_a, actor_type=ActorType.USER, actor_id=None, role=Role.MANAGER)
    result = await tool_registry.execute("medical_tourism.search_providers", {"country": "TR"}, ctx)
    assert result.total == 1
    assert result.providers[0]["name"] == "Istanbul Health Hub"

    # --- Step 10: verify an Agent can use an explicitly permitted Medical
    # Tourism capability -- existing Agent Runtime, zero new execution
    # machinery, real medical_tourism tool (not a stand-in). ---
    agent = await agent_service.create_agent(
        tenant_a, name="Provider Matching Assistant", purpose="medical tourism validation",
        autonomy_tier="EXECUTE_AUTONOMOUS", acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_a, agent.id, tool_name="medical_tourism.search_providers", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_a, agent.id, instructions="Match patients to providers.", created_by=None)
    await agent_service.publish_version(tenant_a, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_a, agent.id)

    execution = await execution_service.run_action(
        tenant_a, agent.id, tool_name="medical_tourism.search_providers", tool_input={"country": "IN"}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.COMPLETED

    # An agent must NOT be able to call a tool it was never granted --
    # publish-snapshot permissions remain authoritative, unchanged Phase 4
    # governance. The existing AgentExecutionService records this as a
    # FAILED execution (not a raised exception) -- the same
    # governance-chain outcome every other agent/tool pairing gets.
    denied = await execution_service.run_action(
        tenant_a, agent.id, tool_name="medical_tourism.create_provider",
        tool_input={"name": "Unauthorized Provider", "country": "US"}, triggered_by=None,
    )
    assert denied.status == AgentExecutionStatus.FAILED
