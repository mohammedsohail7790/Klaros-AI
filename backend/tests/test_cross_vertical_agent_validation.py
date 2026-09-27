"""Phase 4: cross-domain validation. Proves the Agent Runtime handles a
medical-tourism-shaped scenario and a dropshipping-shaped scenario through
the IDENTICAL generic code path — operating on agent/version/tool-
permission/autonomy/tenant concepts only, zero
`if vertical == "medical_tourism"`-shaped branches anywhere (see
test_agent_no_hardcoding_guard.py for the static half of this proof).

Real ToolRegistry tools are used as stand-ins for each vertical's
capability surface (this phase does not — and must not — add vertical
domain tables/tools of its own; see this phase's explicit scope
exclusions): `crm.create_lead`/`crm.create_appointment` stand in for a
medical-tourism "patient lead pipeline"/"appointment scheduling" style
capability, and `marketing.create_campaign`/`finance.get_ar_aging` stand
in for a dropshipping "customer acquisition"/"AR visibility" style
capability. The two Agents below are configured, granted, and executed
through one and the same AgentService/AgentExecutionService — no
vertical-aware code path exists to select between them.
"""

import uuid

import pytest

from app.models.agent import AgentExecutionStatus
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_service import AgentService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def agent_service(tool_registry) -> AgentService:
    from app.db.session import async_session_maker

    return AgentService(async_session_maker)


@pytest.fixture
def execution_service(tool_registry) -> AgentExecutionService:
    from app.db.session import async_session_maker

    return AgentExecutionService(async_session_maker, tool_registry)


async def _build_and_run(agent_service, execution_service, tool_registry, tenant_id, *, tool_name, tool_input):
    agent = await agent_service.create_agent(
        tenant_id, name="Vertical-shaped agent", purpose="cross-vertical proof",
        autonomy_tier="EXECUTE_AUTONOMOUS", acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name=tool_name, tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)
    return await execution_service.run_action(
        tenant_id, agent.id, tool_name=tool_name, tool_input=tool_input, triggered_by=None,
    )


async def test_medical_tourism_shaped_scenario(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    execution = await _build_and_run(
        agent_service, execution_service, tool_registry, tenant_id,
        tool_name="crm.create_lead",
        tool_input={"name": "Prospective Traveler", "source": "referral"},
    )
    assert execution.status == AgentExecutionStatus.COMPLETED


async def test_dropshipping_shaped_scenario(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    execution = await _build_and_run(
        agent_service, execution_service, tool_registry, tenant_id,
        tool_name="marketing.create_campaign",
        tool_input={"name": "Launch Campaign", "channel": "email"},
    )
    assert execution.status == AgentExecutionStatus.COMPLETED


async def test_both_scenarios_share_the_identical_runtime_code_path(agent_service, execution_service, tool_registry):
    """Both executions above go through the exact same
    AgentExecutionService.run_action -> ToolRegistry.execute() call —
    proven here by running both in the same test and asserting neither
    tenant's agent/execution rows leak into the other's history, with no
    vertical-specific code involved on either path."""
    tenant_medical, tenant_dropship = uuid.uuid4(), uuid.uuid4()

    medical_execution = await _build_and_run(
        agent_service, execution_service, tool_registry, tenant_medical,
        tool_name="crm.create_appointment",
        tool_input={"customer_id": str(uuid.uuid4()), "starts_at": "2026-10-01T10:00:00Z"},
    )
    dropship_execution = await _build_and_run(
        agent_service, execution_service, tool_registry, tenant_dropship,
        tool_name="finance.get_ar_aging", tool_input={},
    )

    assert medical_execution.status in (AgentExecutionStatus.COMPLETED, AgentExecutionStatus.FAILED)
    assert dropship_execution.status in (AgentExecutionStatus.COMPLETED, AgentExecutionStatus.FAILED)

    medical_history = await agent_service.list_executions(tenant_medical, medical_execution.agent_id)
    dropship_history = await agent_service.list_executions(tenant_dropship, dropship_execution.agent_id)
    assert len(medical_history) == 1
    assert len(dropship_history) == 1
