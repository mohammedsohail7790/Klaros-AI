"""Phase 8 (Agent Runtime Reliability III): MANDATORY real-Postgres-only
concurrency proofs for orphaned-PENDING-execution recovery — the primary
objective gap this phase closes (see PHASE_8_IMPLEMENTATION_LOG.md §4).
SQLite cannot prove any of these — they depend on genuine row-level
locking under concurrent transactions. Skipped entirely unless
DATABASE_URL points at a real PostgreSQL instance.

Per Phase 7's own instructions (still followed here): a naive 2-way race
is not sufficient evidence of a concurrency property — every race below
uses at least 5 concurrent callers.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.agent import (
    AgentAutonomyTier,
    AgentExecution,
    AgentExecutionMode,
    AgentExecutionStatus,
)
from app.models.rbac import Role
from app.models.tool_policy import TenantToolPolicy
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_recovery_service import AgentRecoveryService, PENDING_ORPHAN_THRESHOLD
from app.services.agent_service import AgentService
from app.services.ai_provider import AICallOutcome, AIProvider
from app.tools.base import ExecutionContext, Tool
from app.tools.policy import ActionPolicy

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_RACE_WIDTH = 5  # never 2 — see module docstring


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        return AICallOutcome(
            success=True, provider=self.name, model=self.model, latency_ms=1,
            raw_text='{"action": "COMPLETE", "final_response": "done", "reasoning_summary": "r", "arguments": {}}',
        )


class _CounterInput(BaseModel):
    pass


class _CounterOutput(BaseModel):
    call_count: int


class CountingTool(Tool):
    """A fake tool for Phase 8's PENDING-recovery proofs only — counts real
    invocations so a test can assert the tool was called AT MOST ONCE
    across an entire concurrent recovery race, never a real external
    provider call."""

    name = "test.pending_recovery_counter"
    description = "test-only counter"
    input_schema = _CounterInput
    output_schema = _CounterOutput
    required_permission = None

    def __init__(self) -> None:
        self.call_count = 0
        self._lock = asyncio.Lock()

    async def execute(self, input: _CounterInput, context: ExecutionContext) -> _CounterOutput:
        async with self._lock:
            self.call_count += 1
            return _CounterOutput(call_count=self.call_count)


async def _allow_auto_policy(tenant_id, tool_name: str) -> None:
    async with async_session_maker() as session:
        session.add(
            TenantToolPolicy(
                tenant_id=tenant_id, tool_name=tool_name, policy=ActionPolicy.AUTO, enabled=True, configured_by=None,
            )
        )
        await session.commit()


async def _active_agent(tool_registry, tenant_id, *, extra_tool: str | None = None):
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="PGPendingRecoveryProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    if extra_tool:
        await agent_service.grant_tool_permission(
            tenant_id, agent.id, tool_name=extra_tool, tool_registry=tool_registry, created_by=None
        )
        await _allow_auto_policy(tenant_id, extra_tool)
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    return agent, version


def _exec_service(tool_registry, worker_id=None):
    return AgentExecutionService(async_session_maker, tool_registry, worker_id=worker_id)


def _recovery(tool_registry, worker_id=None):
    exec_service = _exec_service(tool_registry, worker_id=worker_id)
    rs = AgentReasoningService(async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider())
    return AgentRecoveryService(async_session_maker, rs, worker_id=worker_id, execution_service=exec_service)


async def _orphaned_pending_single_action(tenant_id, agent_id, version_id, *, tool_name: str, age: timedelta):
    """Simulates crash Boundary A/B: an AgentExecution row committed as
    PENDING, then the process died before the RUNNING-transition commit —
    no lease, no AgentExecutionStep row (by construction, per module
    docstring, a step can never exist while status is still PENDING)."""
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent_id, agent_version_id=version_id,
            status=AgentExecutionStatus.PENDING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name=tool_name, tool_input_summary={},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        # created_at has a server/model default of "now" — backdate it
        # directly to simulate an orphan old enough to be swept.
        execution.created_at = datetime.now(timezone.utc) - age
        await session.commit()
        return execution.id


async def _orphaned_pending_reasoning(tenant_id, agent_id, version_id, *, age: timedelta):
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent_id, agent_version_id=version_id,
            status=AgentExecutionStatus.PENDING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g", reasoning_state={"goal": "g", "history": []},
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution.created_at = datetime.now(timezone.utc) - age
        await session.commit()
        return execution.id


@requires_real_postgres
async def test_orphaned_pending_single_action_is_recovered_and_tool_runs_exactly_once(tool_registry) -> None:
    """Crash Boundary A/B for SINGLE_ACTION: a PENDING row old enough to be
    orphaned is claimed by the sweep, transitions to RUNNING, and the tool
    is invoked exactly once (never zero — it's provably safe since no step
    could ever have been written while PENDING; never twice)."""
    tenant_id = uuid.uuid4()
    tool = CountingTool()
    tool_registry.register(tool)
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.pending_recovery_counter")
    execution_id = await _orphaned_pending_single_action(
        tenant_id, agent.id, version.id, tool_name="test.pending_recovery_counter",
        age=PENDING_ORPHAN_THRESHOLD + timedelta(seconds=5),
    )

    recovery = _recovery(tool_registry, worker_id=uuid.uuid4())
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed
    assert tool.call_count == 1

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED
        assert execution.recovery_attempt_count == 1


@requires_real_postgres
async def test_five_way_recovery_race_on_orphaned_pending_exactly_one_worker_wins(tool_registry) -> None:
    """5 concurrent recovery workers race for the SAME orphaned PENDING
    execution — the atomic conditional UPDATE claim (extended this phase to
    also match PENDING, see AgentRecoveryService._claim) must let exactly
    one worker proceed, and the tool must be invoked exactly once total
    across all 5 — never zero, never more than one."""
    tenant_id = uuid.uuid4()
    tool = CountingTool()
    tool_registry.register(tool)
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.pending_recovery_counter")
    execution_id = await _orphaned_pending_single_action(
        tenant_id, agent.id, version.id, tool_name="test.pending_recovery_counter",
        age=PENDING_ORPHAN_THRESHOLD + timedelta(seconds=5),
    )

    workers = [_recovery(tool_registry, worker_id=uuid.uuid4()) for _ in range(_RACE_WIDTH)]
    results = await asyncio.gather(*(w.sweep_once(tenant_id) for w in workers))
    claimed_by = [r for r in results if execution_id in r]
    assert len(claimed_by) == 1
    assert tool.call_count == 1

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.recovery_attempt_count == 1
        assert execution.status == AgentExecutionStatus.COMPLETED


@requires_real_postgres
async def test_fresh_pending_execution_is_never_claimed(tool_registry) -> None:
    """Negative control: a PENDING row that is NOT yet older than
    PENDING_ORPHAN_THRESHOLD (i.e. a genuinely live, in-flight request that
    simply hasn't reached RUNNING yet) must never be claimed by a sweep —
    the tool must never be invoked, and the row must remain untouched."""
    tenant_id = uuid.uuid4()
    tool = CountingTool()
    tool_registry.register(tool)
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.pending_recovery_counter")
    execution_id = await _orphaned_pending_single_action(
        tenant_id, agent.id, version.id, tool_name="test.pending_recovery_counter",
        age=timedelta(seconds=1),  # far younger than PENDING_ORPHAN_THRESHOLD
    )

    recovery = _recovery(tool_registry, worker_id=uuid.uuid4())
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id not in claimed
    assert tool.call_count == 0

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.PENDING
        assert execution.execution_owner_id is None
        assert execution.recovery_attempt_count == 0


@requires_real_postgres
async def test_orphaned_pending_reasoning_execution_is_recovered(tool_registry) -> None:
    """Crash Boundary A/B for REASONING mode: an orphaned PENDING row (no
    step could exist yet, by construction) is claimed and resumed through
    the normal `_run_loop`, which behaves exactly as a fresh `start()`
    would since step_count is 0."""
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id)
    execution_id = await _orphaned_pending_reasoning(
        tenant_id, agent.id, version.id, age=PENDING_ORPHAN_THRESHOLD + timedelta(seconds=5),
    )

    recovery = _recovery(tool_registry, worker_id=uuid.uuid4())
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED


@requires_real_postgres
async def test_recovery_sweep_tenant_scoped_never_claims_another_tenants_pending_row(tool_registry) -> None:
    """A tenant-scoped sweep (the normal production call shape — one sweep
    per tenant, or `tenant_id=None` for an operator-level global sweep)
    must never claim a different tenant's orphaned PENDING execution when a
    tenant_id is supplied."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    tool = CountingTool()
    tool_registry.register(tool)
    agent_b, version_b = await _active_agent(tool_registry, tenant_b, extra_tool="test.pending_recovery_counter")
    execution_id_b = await _orphaned_pending_single_action(
        tenant_b, agent_b.id, version_b.id, tool_name="test.pending_recovery_counter",
        age=PENDING_ORPHAN_THRESHOLD + timedelta(seconds=5),
    )

    recovery = _recovery(tool_registry, worker_id=uuid.uuid4())
    claimed = await recovery.sweep_once(tenant_a)  # sweeping tenant A only
    assert execution_id_b not in claimed
    assert tool.call_count == 0

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id_b)
        assert execution.status == AgentExecutionStatus.PENDING  # untouched
