"""Phase 6 (Agent Runtime Reliability): cross-vertical validation for the
scheduled/event trigger runtime. Per this phase's instructions, these are
TEST SCENARIOS ONLY — no medical-tourism or dropshipping database model,
table, or `if vertical == ...` branch is introduced anywhere in Phase 6
production code (statically enforced by
test_agent_no_hardcoding_guard.py's extended file list). Both scenarios
below run through the exact same generic `AgentTriggerService`/
`register_agent_trigger_handlers` code path as any other agent trigger —
only the goal text and event payload shape differ, exactly as a real
tenant's own configuration would.
"""

import json
import uuid

import pytest

from app.events.agent_trigger_handlers import register_agent_trigger_handlers
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.agent import AgentAutonomyTier, AgentExecutionStatus, AgentStatus
from app.models.event import EventType
from app.models.rbac import Role
from app.services.agent_service import AgentService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        text = json.dumps({"action": "COMPLETE", "final_response": "handled", "reasoning_summary": "r", "arguments": {}})
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


async def _agent_with_event_trigger(tool_registry, tenant_id, *, event_type, goal):
    from app.db.session import async_session_maker

    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="VerticalProbeAgent", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x",
        triggers={"event": {"enabled": True, "event_type": event_type.value, "goal": goal}},
        created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    return agent_service, await agent_service.activate(tenant_id, agent.id), version


async def _executions_for_tenant(tenant_id):
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(select(AgentExecution).where(AgentExecution.tenant_id == tenant_id))).scalars().all()
        return rows


async def test_medical_tourism_shaped_lead_qualification_scenario(tool_registry):
    """"New patient inquiry event -> qualify lead -> schedule consultation"
    — modeled here purely as a generic Lead-shaped event/goal, through the
    exact same generic trigger dispatcher as every other scenario. No
    Patient/Provider/Consultation model exists or is needed."""
    tenant_id = uuid.uuid4()
    from app.db.session import async_session_maker

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_agent_trigger_handlers(bus, async_session_maker, tool_registry, FakeAIProvider())
    await _agent_with_event_trigger(
        tool_registry, tenant_id, event_type=EventType.LEAD_CREATED,
        goal="Qualify this new patient inquiry lead and propose a consultation time.",
    )
    await bus.publish(
        tenant_id=tenant_id, event_type=EventType.LEAD_CREATED, source="test",
        payload={"service_requested": "consultation"},
    )
    await bus.process_pending(EventType.LEAD_CREATED, count=10)

    executions = await _executions_for_tenant(tenant_id)
    assert len(executions) == 1
    assert executions[0].status == AgentExecutionStatus.COMPLETED
    assert executions[0].trigger_source == "EVENT"


async def test_dropshipping_shaped_order_verification_scenario(tool_registry):
    """"New order event -> verify inventory -> notify fulfillment" —
    modeled here as a generic job-shaped event/goal (no Order/SKU/Supplier
    model exists yet; out of Phase 6's hard scope per instructions), still
    through the identical generic dispatcher."""
    tenant_id = uuid.uuid4()
    from app.db.session import async_session_maker

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_agent_trigger_handlers(bus, async_session_maker, tool_registry, FakeAIProvider())
    await _agent_with_event_trigger(
        tool_registry, tenant_id, event_type=EventType.JOB_CREATED,
        goal="A new order-shaped job was created — verify stock levels and notify the fulfillment queue.",
    )
    await bus.publish(tenant_id=tenant_id, event_type=EventType.JOB_CREATED, source="test", payload={})
    await bus.process_pending(EventType.JOB_CREATED, count=10)

    executions = await _executions_for_tenant(tenant_id)
    assert len(executions) == 1
    assert executions[0].status == AgentExecutionStatus.COMPLETED


async def test_both_scenarios_share_one_code_path_no_vertical_branch(tool_registry):
    """Both scenarios above dispatch through `register_agent_trigger_handlers`'s
    SINGLE `agent_event_trigger_dispatch` closure — proven directly by
    subscribing once and firing both event types through it, with no
    per-vertical setup difference beyond ordinary tenant configuration
    (event_type/goal), which is exactly how a real tenant would configure
    two different Agents."""
    tenant_id = uuid.uuid4()
    from app.db.session import async_session_maker

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_agent_trigger_handlers(bus, async_session_maker, tool_registry, FakeAIProvider())
    await _agent_with_event_trigger(
        tool_registry, tenant_id, event_type=EventType.LEAD_CREATED, goal="medical-tourism-shaped goal",
    )
    await _agent_with_event_trigger(
        tool_registry, tenant_id, event_type=EventType.JOB_CREATED, goal="dropshipping-shaped goal",
    )
    await bus.publish(tenant_id=tenant_id, event_type=EventType.LEAD_CREATED, source="test", payload={})
    await bus.publish(tenant_id=tenant_id, event_type=EventType.JOB_CREATED, source="test", payload={})
    await bus.process_pending(EventType.LEAD_CREATED, count=10)
    await bus.process_pending(EventType.JOB_CREATED, count=10)

    executions = await _executions_for_tenant(tenant_id)
    assert len(executions) == 2
    assert {e.status for e in executions} == {AgentExecutionStatus.COMPLETED}
