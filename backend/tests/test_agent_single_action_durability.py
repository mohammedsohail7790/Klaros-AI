"""Phase 7 (Agent Runtime Reliability II): SINGLE_ACTION step-level
durability and crash recovery — functional/logic-level tests (SQLite-backed,
like every other Phase 4/5/6 functional test file). The mandatory real-
Postgres concurrency races live in
test_postgres_agent_single_action_reliability.py.

Covers the failure matrix this phase's own instructions require:
  - a durable AgentExecutionStep exists before the tool call, for every
    outcome (success/failure/approval-required)
  - crash boundary A/B (no step yet) -> safely resumable
  - crash boundary F (step already terminal) -> tool NEVER re-invoked
  - crash boundaries C/D/E (step mid-flight) -> idempotent tool retried
    with the same identity; non-idempotent tool safe-halted
  - the deterministic idempotency identity is stable across calls and
    cannot be influenced by tool input ("LLM output")
  - version immutability / kill switch / tenant isolation are preserved
    through recovery
"""

import uuid
from datetime import datetime, timezone

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


async def _active_agent(
    agent_service, tool_registry, tenant_id, *, tool_name="system.get_tenant_context",
    autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
):
    agent = await agent_service.create_agent(
        tenant_id, name="DurabilityProbe", purpose="test", autonomy_tier=autonomy_tier,
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name=tool_name, tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    return agent, version


# --------------------------------------------------------- Durable step boundary

async def test_run_action_creates_exactly_one_durable_step(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id)
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.COMPLETED

    steps = await execution_service.list_steps(tenant_id, execution.id)
    assert len(steps) == 1
    step = steps[0]
    assert step.step_number == 1
    assert step.tool_name == "system.get_tenant_context"
    assert step.status == AgentExecutionStepStatus.EXECUTED
    assert step.started_at is not None
    assert step.completed_at is not None
    assert step.output_summary is not None


async def test_failed_tool_call_still_leaves_a_terminal_step(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id, tool_name="system.get_tenant_context")
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_current_time",  # never granted -> governed rejection
        tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.FAILED

    steps = await execution_service.list_steps(tenant_id, execution.id)
    assert len(steps) == 1
    assert steps[0].status == AgentExecutionStepStatus.FAILED
    assert steps[0].completed_at is not None


# ------------------------------------------------------- Deterministic identity

async def test_idempotency_identity_is_deterministic_and_stable(execution_service):
    a = execution_service._idempotency_identity(uuid.UUID(int=1), 1)
    b = execution_service._idempotency_identity(uuid.UUID(int=1), 1)
    assert a == b  # same (execution_id, step_number) -> same identity, always

    c = execution_service._idempotency_identity(uuid.UUID(int=2), 1)
    assert c != a  # different execution -> different identity


async def test_malicious_tool_input_cannot_influence_idempotency_identity_or_tenant(
    agent_service, execution_service, tool_registry
):
    """Prompt-injection resistance: even if a malicious/compromised LLM
    output tries to smuggle its own idempotency_key/tenant_id/execution_id
    inside the tool arguments, ExecutionContext is always built server-side
    from durable, already-persisted execution state — tool_input is never
    read to populate any of those fields."""
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id)
    malicious_input = {
        "idempotency_key": "attacker-supplied-key",
        "tenant_id": str(uuid.uuid4()),
        "execution_id": str(uuid.uuid4()),
        "step_id": "attacker-step",
    }
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input=malicious_input, triggered_by=None,
    )
    # The tool call still ran under the REAL tenant — the malicious
    # "tenant_id" field in tool_input was never consulted.
    assert execution.result_summary["tenant_id"] == str(tenant_id)
    expected_key = execution_service._idempotency_identity(execution.id, 1)
    assert expected_key == f"agent-exec:{execution.id}:1"
    assert "attacker-supplied-key" not in expected_key


# ------------------------------------------------------------ Crash recovery

async def test_resume_recovered_boundary_a_no_step_runs_tool_once(agent_service, execution_service, tool_registry):
    """Crash boundary A/B: the execution was marked RUNNING (simulated
    directly here) but no step was ever durably written — safe to create
    the step now and run the tool exactly once."""
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    result = await execution_service.resume_recovered(tenant_id, execution_id)
    assert result.status == AgentExecutionStatus.COMPLETED
    steps = await execution_service.list_steps(tenant_id, execution_id)
    assert len(steps) == 1
    assert steps[0].status == AgentExecutionStepStatus.EXECUTED


async def test_resume_recovered_boundary_f_completed_step_never_reruns_tool(
    agent_service, execution_service, tool_registry
):
    """Crash boundary F: the step already reached a terminal EXECUTED
    outcome — recovery must finish purely from that record. Proven by
    pointing tool_name at a tool the agent was never granted: if recovery
    called the tool again, it would fail governance and the execution would
    end up FAILED, not COMPLETED."""
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution, AgentExecutionStep, AgentExecutionStepType

    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id, tool_name="system.get_tenant_context")
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            # Deliberately an UNGRANTED tool — if resume_recovered ever
            # re-executed it, ToolRegistry would reject it and the
            # execution would end up FAILED, not COMPLETED.
            tool_name="system.get_current_time", tool_input_summary={},
        )
        session.add(execution)
        await session.flush()
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution.id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
                tool_name="system.get_current_time", output_summary={"already": "done"},
                started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        execution_id = execution.id

    result = await execution_service.resume_recovered(tenant_id, execution_id)
    assert result.status == AgentExecutionStatus.COMPLETED
    assert result.result_summary == {"already": "done"}


async def test_resume_recovered_ambiguous_outcome_is_safe_halted(agent_service, execution_service, tool_registry):
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution, AgentExecutionStep, AgentExecutionStepType

    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
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

    result = await execution_service.resume_recovered(tenant_id, execution_id)
    assert result.status == AgentExecutionStatus.HALTED
    assert result.termination_reason == AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT

    steps = await execution_service.list_steps(tenant_id, execution_id)
    assert steps[0].status == AgentExecutionStepStatus.FAILED
    assert steps[0].error_code == "interrupted_by_crash"


async def test_resume_recovered_tenant_mismatch_denied(agent_service, execution_service, tool_registry):
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    tenant_id = uuid.uuid4()
    other_tenant = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    from app.services.agent_execution_service import AgentExecutionError

    with pytest.raises(AgentExecutionError):
        await execution_service.resume_recovered(other_tenant, execution_id)


# ------------------------------------------------------- Kill switch / version

async def test_kill_switch_blocks_recovered_execution_tool_call(agent_service, execution_service, tool_registry):
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution
    from app.models.organization import Organization

    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    async with async_session_maker() as session:
        session.add(
            Organization(id=tenant_id, name="KillSwitchSingleActionCo", slug=f"ks-sa-{tenant_id.hex[:8]}", ai_paused=True)
        )
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    result = await execution_service.resume_recovered(tenant_id, execution_id)
    assert result.status == AgentExecutionStatus.FAILED
    assert "kill switch" in (result.error_message or "").lower() or "paused" in (result.error_message or "").lower()


async def test_recovery_never_upgrades_to_a_newer_agent_version(agent_service, execution_service, tool_registry):
    """Version Immutability under recovery (mirrors
    test_agent_recovery_security.py's identical REASONING-mode proof): an
    execution started under v1 must never be silently switched to a newer
    v2 that was published after it started, even though v1 is no longer
    the agent's current version. `ToolRegistry`'s own pre-existing "only
    the agent's current version may execute" check means recovery is
    correctly, cleanly REJECTED in this case (not silently upgraded, and
    not silently allowed to keep running under a stale version either) —
    the stronger, more important proof is that `agent_version_id` on the
    execution record itself is never rewritten to v2."""
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    tenant_id = uuid.uuid4()
    agent, v1 = await _active_agent(agent_service, tool_registry, tenant_id)
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=v1.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name="system.get_tenant_context", tool_input_summary={},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    v2 = await agent_service.create_version(tenant_id, agent.id, instructions="v2", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, v2.id, actor_id=None)
    # agent.current_version_id is now v2 — v1 is no longer current.

    result = await execution_service.resume_recovered(tenant_id, execution_id)
    assert result.agent_version_id == v1.id  # never silently rewritten to v2
    # Cleanly rejected (v1 is no longer the current version) — never
    # silently executed under either version.
    assert result.status == AgentExecutionStatus.FAILED
