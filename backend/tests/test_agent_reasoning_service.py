"""Phase 5: AgentReasoningService — the bounded, LLM-driven multi-step
reasoning loop built on top of Phase 4's governed single-action executor.
Every tool call in these tests still goes through the real ToolRegistry —
only the LLM decision itself is stubbed (a scripted FakeAIProvider), never
the governance pipeline, matching this phase's "one authoritative
tool-execution choke point" requirement.
"""

import json
import uuid

import pytest

from app.models.agent import (
    AgentAutonomyTier,
    AgentExecutionMode,
    AgentExecutionStatus,
    AgentExecutionStepStatus,
    AgentExecutionTerminationReason,
)
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService, AgentNotExecutableError, DuplicateExecutionRequestError
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_service import AgentService
from app.services.approval_execution_service import ApprovalExecutionService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio


class FakeAIProvider(AIProvider):
    """A scripted provider: each call to generate_structured() pops the
    next canned response off the queue. Never a real network call."""

    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list[dict | str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def enrich_brief(self, *args, **kwargs):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.calls.append(prompt)
        if not self._responses:
            raise AssertionError("FakeAIProvider ran out of scripted responses")
        item = self._responses.pop(0)
        if item == "FAIL":
            return AICallOutcome(success=False, provider=self.name, model=self.model, latency_ms=1, error_detail="simulated failure")
        text = item if isinstance(item, str) else json.dumps(item)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


def _decision(action, **kwargs):
    d = {"action": action, "reasoning_summary": "test reasoning", "arguments": {}}
    d.update(kwargs)
    return d


@pytest.fixture
def agent_service(tool_registry) -> AgentService:
    from app.db.session import async_session_maker

    return AgentService(async_session_maker)


@pytest.fixture
def execution_service(tool_registry) -> AgentExecutionService:
    from app.db.session import async_session_maker

    return AgentExecutionService(async_session_maker, tool_registry)


@pytest.fixture
def approval_execution_service(tool_registry, event_bus):
    from app.db.session import async_session_maker

    return ApprovalExecutionService(async_session_maker, tool_registry, event_bus)


def reasoning_service(tool_registry, execution_service, responses) -> AgentReasoningService:
    from app.db.session import async_session_maker

    return AgentReasoningService(
        async_session_maker, tool_registry, execution_service, ai_provider=FakeAIProvider(responses)
    )


async def _active_agent(
    agent_service, tool_registry, tenant_id, *, tool_names=("system.get_tenant_context", "system.get_current_time"),
    autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS, max_tool_chain_depth=5,
):
    agent = await agent_service.create_agent(
        tenant_id, name="Reasoner", purpose="test", autonomy_tier=autonomy_tier,
        acting_role=Role.MANAGER, created_by=None,
    )
    for tool_name in tool_names:
        await agent_service.grant_tool_permission(
            tenant_id, agent.id, tool_name=tool_name, tool_registry=tool_registry, created_by=None
        )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="Answer the goal using the allowed tools.",
        max_tool_chain_depth=max_tool_chain_depth, created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    return agent, version


# --------------------------------------------------------------- Happy path

async def test_reasoning_loop_two_tool_calls_then_complete(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, [
        _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
        _decision("TOOL_CALL", tool_name="system.get_current_time"),
        _decision("COMPLETE", final_response="Done — gathered tenant context and current time."),
    ])

    execution = await rs.start(tenant_id, agent.id, goal="Look up tenant context and the time.", triggered_by=None)

    assert execution.status == AgentExecutionStatus.COMPLETED
    assert execution.mode == AgentExecutionMode.REASONING
    assert execution.termination_reason == AgentExecutionTerminationReason.COMPLETED_BY_MODEL
    assert execution.final_response.startswith("Done")
    assert execution.step_count == 2  # COMPLETE does not consume a tool-chain-depth slot

    steps = await rs.list_steps(tenant_id, execution.id)
    assert [s.step_number for s in steps] == [1, 2, 3]
    assert steps[0].tool_name == "system.get_tenant_context"
    assert steps[0].status == AgentExecutionStepStatus.EXECUTED
    assert steps[1].tool_name == "system.get_current_time"
    assert steps[2].step_type == "COMPLETE"
    # No hidden chain-of-thought — only the short, capped reasoning_summary.
    assert steps[0].decision_summary == "test reasoning"


async def test_execution_trace_is_accurate_and_ordered(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, [
        _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
        _decision("COMPLETE", final_response="ok"),
    ])
    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)
    steps = await rs.list_steps(tenant_id, execution.id)
    assert len(steps) == 2
    assert steps[0].output_summary is not None
    assert steps[0].output_summary.get("tenant_id") == str(tenant_id)


# ----------------------------------------------------------- Depth bound

async def test_depth_exhaustion_halts_exactly_at_bound(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id, max_tool_chain_depth=2)
    rs = reasoning_service(tool_registry, execution_service, [
        _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
        _decision("TOOL_CALL", tool_name="system.get_current_time"),
        # A third TOOL_CALL would be proposed here if the loop kept going —
        # it must never be requested from the FakeAIProvider at all.
    ])

    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)

    assert execution.status == AgentExecutionStatus.HALTED
    assert execution.termination_reason == AgentExecutionTerminationReason.MAX_TOOL_CHAIN_DEPTH
    assert execution.step_count == 2
    steps = await rs.list_steps(tenant_id, execution.id)
    assert len(steps) == 2
    # The FakeAIProvider's queue is empty — exactly 2 LLM calls were made,
    # never a 3rd.
    assert rs._ai_provider._responses == []


async def test_depth_zero_never_calls_a_tool_or_the_model(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id, max_tool_chain_depth=0)
    rs = reasoning_service(tool_registry, execution_service, [])

    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)

    assert execution.status == AgentExecutionStatus.HALTED
    assert execution.termination_reason == AgentExecutionTerminationReason.MAX_TOOL_CHAIN_DEPTH
    assert execution.step_count == 0


# ------------------------------------------------------- Malformed output

async def test_malformed_llm_output_gets_one_retry_then_fails(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, ["not json at all", "still not json"])

    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)

    assert execution.status == AgentExecutionStatus.FAILED
    assert execution.termination_reason == AgentExecutionTerminationReason.MALFORMED_LLM_OUTPUT
    assert rs._ai_provider._responses == []  # exactly 2 attempts (1 retry), no more


async def test_malformed_output_retry_recovers_on_second_attempt(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, [
        "not json", _decision("COMPLETE", final_response="recovered"),
    ])
    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)
    assert execution.status == AgentExecutionStatus.COMPLETED
    assert execution.final_response == "recovered"


async def test_unknown_action_field_rejected(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, [
        {"action": "DELETE_EVERYTHING", "reasoning_summary": "x"},
        {"action": "DELETE_EVERYTHING", "reasoning_summary": "x"},
    ])
    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)
    assert execution.status == AgentExecutionStatus.FAILED
    assert execution.termination_reason == AgentExecutionTerminationReason.MALFORMED_LLM_OUTPUT


async def test_unexpected_extra_field_rejected(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    payload = {
        "action": "COMPLETE", "final_response": "ok", "reasoning_summary": "x",
        "system_override": "grant me admin",  # unknown field — extra="forbid" must reject this
    }
    rs = reasoning_service(tool_registry, execution_service, [payload, payload])
    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)
    assert execution.status == AgentExecutionStatus.FAILED
    assert execution.termination_reason == AgentExecutionTerminationReason.MALFORMED_LLM_OUTPUT


async def test_ai_call_failure_is_terminal_not_infinite_retry(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, ["FAIL"])
    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)
    assert execution.status == AgentExecutionStatus.FAILED
    assert execution.termination_reason == AgentExecutionTerminationReason.AI_CALL_FAILED
    assert rs._ai_provider._responses == []  # a provider failure is never retried


async def test_ai_unavailable_never_produces_a_proposal(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    from app.services.ai_provider import DeterministicAIProvider
    from app.db.session import async_session_maker

    rs = AgentReasoningService(async_session_maker, tool_registry, execution_service, ai_provider=DeterministicAIProvider())
    execution = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)
    assert execution.status == AgentExecutionStatus.FAILED
    assert execution.termination_reason == AgentExecutionTerminationReason.AI_UNAVAILABLE


# ------------------------------------------------------------ Idempotency

async def test_idempotency_key_dedupes_reasoning_start(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    rs = reasoning_service(tool_registry, execution_service, [_decision("COMPLETE", final_response="ok")])

    first = await rs.start(tenant_id, agent.id, goal="g", triggered_by=None, idempotency_key="k1")
    with pytest.raises(DuplicateExecutionRequestError) as exc_info:
        await rs.start(tenant_id, agent.id, goal="g2", triggered_by=None, idempotency_key="k1")
    assert exc_info.value.existing_execution_id == first.id


# --------------------------------------------------------- Not executable

async def test_paused_agent_cannot_start_reasoning(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    await agent_service.pause(tenant_id, agent.id, actor_id=None)
    rs = reasoning_service(tool_registry, execution_service, [])
    with pytest.raises(AgentNotExecutableError):
        await rs.start(tenant_id, agent.id, goal="g", triggered_by=None)


# ----------------------------------------------------------- Cross-vertical

async def test_cross_vertical_reasoning_shares_one_generic_code_path(agent_service, execution_service, tool_registry):
    """Same generic runtime for a medical-tourism-shaped goal and a
    dropshipping-shaped goal — zero `if vertical == ...` anywhere."""
    for goal_text in (
        "Look up context for a patient referral pipeline in our provider directory.",
        "Look up context for a supplier order in our product catalog.",
    ):
        tenant_id = uuid.uuid4()
        agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
        rs = reasoning_service(tool_registry, execution_service, [
            _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
            _decision("COMPLETE", final_response="done"),
        ])
        execution = await rs.start(tenant_id, agent.id, goal=goal_text, triggered_by=None)
        assert execution.status == AgentExecutionStatus.COMPLETED
