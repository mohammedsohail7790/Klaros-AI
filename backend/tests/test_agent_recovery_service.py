"""Phase 6 (Agent Runtime Reliability): `AgentRecoveryService` — crash
recovery for interrupted REASONING-mode `AgentExecution` rows. Functional/
logic-level tests (SQLite-backed, like every other Phase 4/5 functional
test file); the mandatory real-Postgres concurrency races (double-claim,
heartbeat-not-stolen) live in test_postgres_agent_recovery.py.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.agent import (
    AgentAutonomyTier,
    AgentExecutionMode,
    AgentExecutionStatus,
    AgentExecutionStepStatus,
    AgentExecutionTerminationReason,
)
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_recovery_service import MAX_RECOVERY_ATTEMPTS, AgentRecoveryService
from app.services.agent_service import AgentService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        if not self._responses:
            raise AssertionError("FakeAIProvider ran out of scripted responses")
        item = self._responses.pop(0)
        text = item if isinstance(item, str) else json.dumps(item)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


def _decision(action, **kwargs):
    d = {"action": action, "reasoning_summary": "r", "arguments": {}}
    d.update(kwargs)
    return d


async def _active_agent(
    tool_registry, tenant_id, *, tool_names=("system.get_tenant_context", "system.get_current_time"),
    max_tool_chain_depth=5,
):
    from app.db.session import async_session_maker

    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="RecoveryProbe", purpose="test", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    for tool_name in tool_names:
        await agent_service.grant_tool_permission(
            tenant_id, agent.id, tool_name=tool_name, tool_registry=tool_registry, created_by=None
        )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="Use the allowed tools.", max_tool_chain_depth=max_tool_chain_depth,
        created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    return agent_service, agent, version


def _reasoning(tool_registry, responses, *, worker_id=None):
    from app.db.session import async_session_maker

    exec_service = AgentExecutionService(async_session_maker, tool_registry)
    return AgentReasoningService(
        async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider(responses), worker_id=worker_id
    )


async def _force_stale(execution_id, *, lease_expires_at=None, recovery_attempt_count=None):
    """Test-only helper: directly backdate an execution's lease (or set its
    recovery_attempt_count) to simulate "the owning process crashed a while
    ago" without actually killing a process — this is the controlled
    crash-injection technique this phase's instructions call for."""
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        if lease_expires_at is not None:
            execution.lease_expires_at = lease_expires_at
        if recovery_attempt_count is not None:
            execution.recovery_attempt_count = recovery_attempt_count
        await session.commit()


# --------------------------------------------------------- Recovery safety matrix

async def test_fresh_running_execution_is_not_recovered(tool_registry):
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    rs = _reasoning(tool_registry, [_decision("TOOL_CALL", tool_name="system.get_tenant_context")])
    # A single-step chain (max depth 1) that pauses forever isn't easy to
    # construct without approval; instead assert directly on a RUNNING row
    # with a lease comfortably in the future — the sweep must skip it.
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g",
            execution_owner_id=uuid.uuid4(),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id not in claimed


async def test_expired_lease_execution_is_recoverable(tool_registry):
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g", reasoning_state={"goal": "g", "history": []},
            execution_owner_id=uuid.uuid4(),
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    rs = _reasoning(tool_registry, [_decision("COMPLETE", final_response="recovered and done")])
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED
        assert execution.final_response == "recovered and done"


@pytest.mark.parametrize("terminal_status", [
    AgentExecutionStatus.WAITING_APPROVAL, AgentExecutionStatus.COMPLETED,
    AgentExecutionStatus.FAILED, AgentExecutionStatus.HALTED,
])
async def test_non_running_executions_are_never_recovered(tool_registry, terminal_status):
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=terminal_status, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # deliberately "expired"
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    rs = _reasoning(tool_registry, [])  # no scripted responses — must never be called
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id not in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == terminal_status  # untouched


async def test_single_action_no_step_yet_is_safely_resumed_and_runs_once(tool_registry):
    """Phase 7: crash boundary A/B — the execution was marked RUNNING but no
    AgentExecutionStep was ever durably recorded (nothing happened yet), so
    recovery may safely create the step and run the tool exactly once,
    through the exact same governed ToolRegistry.execute() path."""
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    rs = _reasoning(tool_registry, [])  # never called — this is a SINGLE_ACTION execution
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED

    steps = await rs.execution_service.list_steps(tenant_id, execution_id)
    assert len(steps) == 1
    assert steps[0].step_number == 1
    assert steps[0].status == AgentExecutionStepStatus.EXECUTED
    assert steps[0].completed_at is not None


async def test_single_action_completed_step_is_never_reexecuted(tool_registry):
    """Phase 7: crash boundary F — the step already reached a terminal
    EXECUTED outcome before the crash (only the AgentExecution row's own
    terminal status was never persisted). Recovery must finish the
    execution from the step's own durable record and must NEVER call the
    tool again — proven here by pointing tool_name at a tool that isn't
    even granted to the agent, so a second invocation would raise."""
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution, AgentExecutionStep, AgentExecutionStepType

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.flush()
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution.id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
                tool_name="system.get_tenant_context",
                output_summary={"already": "done"},
                started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        execution_id = execution.id

    rs = _reasoning(tool_registry, [])  # never called
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED
        assert execution.result_summary == {"already": "done"}


async def test_single_action_ambiguous_non_idempotent_outcome_is_safe_halted(tool_registry):
    """Phase 7: crash boundaries C/D/E — the step was left mid-flight
    (started, never completed). system.get_tenant_context has no verified
    `supports_idempotency` (it defaults to False), so per the Unknown
    External Outcome policy the runtime must never guess: it safe-halts
    instead of retrying the tool."""
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    from app.db.session import async_session_maker
    from app.models.agent import (
        AgentExecution,
        AgentExecutionStep,
        AgentExecutionTerminationReason as TR,
        AgentExecutionStepType,
    )

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.flush()
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution.id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
                tool_name="system.get_tenant_context",
                started_at=datetime.now(timezone.utc), completed_at=None,  # mid-flight
            )
        )
        await session.commit()
        execution_id = execution.id

    rs = _reasoning(tool_registry, [])  # never called
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.HALTED
        assert execution.termination_reason == TR.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT

    steps = await rs.execution_service.list_steps(tenant_id, execution_id)
    assert steps[0].status == AgentExecutionStepStatus.FAILED
    assert steps[0].error_code == "interrupted_by_crash"


async def test_poison_execution_halted_after_max_recovery_attempts(tool_registry):
    """A deterministic execution that "always crashes at the same point":
    simulated here by an execution whose recovery_attempt_count already
    exceeds MAX_RECOVERY_ATTEMPTS — the sweep must halt it, never claim/
    resume it again, bounding a crash->recover->crash loop."""
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(tool_registry, tenant_id)
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution, AgentExecutionTerminationReason as TR

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            recovery_attempt_count=MAX_RECOVERY_ATTEMPTS,  # this claim pushes it to MAX+1
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    rs = _reasoning(tool_registry, [])  # never called
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.FAILED
        assert execution.termination_reason == TR.RECOVERY_ATTEMPTS_EXHAUSTED


async def test_recovery_reconciles_interrupted_step_never_reexecutes_tool(tool_registry):
    """Controlled crash-point injection (per this phase's instructions):
    simulate "tool started but its outcome was never recorded" by writing a
    pending AgentExecutionStep row by hand (exactly the shape
    `_attempt_tool_call`'s initial `_write_step(..., mark_pending=True)`
    call leaves), then recovering. The interrupted step must be marked
    FAILED/interrupted_by_crash and the tool for that step must NEVER be
    invoked again by the resumed loop — proven by scripting the AI
    provider's next response to a DIFFERENT tool than the interrupted one,
    and asserting exactly that tool (not the interrupted one) executes."""
    tenant_id = uuid.uuid4()
    agent_service, agent, version = await _active_agent(
        tool_registry, tenant_id, tool_names=("system.get_tenant_context", "system.get_current_time"),
        max_tool_chain_depth=5,
    )
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution, AgentExecutionStep, AgentExecutionStepStatus as StepStatus, AgentExecutionStepType

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name="system.get_tenant_context", tool_input_summary={}, goal="g",
            reasoning_state={"goal": "g", "history": []}, step_count=1,
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.flush()
        # The "interrupted" step: written before the tool call, never
        # updated with an outcome (completed_at is NULL) — the durable
        # signature _reconcile_interrupted_step looks for.
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution.id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=StepStatus.EXECUTED,
                tool_name="system.get_tenant_context", started_at=datetime.now(timezone.utc), completed_at=None,
            )
        )
        await session.commit()
        execution_id = execution.id

    rs = _reasoning(tool_registry, [
        _decision("TOOL_CALL", tool_name="system.get_current_time"),
        _decision("COMPLETE", final_response="done after recovery"),
    ])
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED

    steps = await rs.list_steps(tenant_id, execution_id)
    step_by_number = {s.step_number: s for s in steps}
    assert step_by_number[1].status == StepStatus.FAILED
    assert step_by_number[1].error_code == "interrupted_by_crash"
    assert step_by_number[1].tool_name == "system.get_tenant_context"  # never re-run
    assert step_by_number[2].tool_name == "system.get_current_time"  # the NEXT step, not a re-attempt
    assert step_by_number[2].status == StepStatus.EXECUTED
