"""Phase 7 (Agent Runtime Reliability II): MANDATORY real-Postgres-only
concurrency proofs for SINGLE_ACTION step durability and tool idempotency.
SQLite cannot prove any of these — they depend on genuine row-level locking
under concurrent transactions. Skipped entirely unless DATABASE_URL points
at a real PostgreSQL instance.

Per this phase's own instructions: a naive 2-way race is not sufficient
evidence (Phase 6's own check-then-insert bug needed a 5-way race to
reproduce — a 2-way race passed even with the bug present). Every race
below uses at least 5 concurrent callers.
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
    AgentExecutionStep,
    AgentExecutionStepStatus,
    AgentExecutionStepType,
    AgentExecutionTerminationReason,
)
from app.models.rbac import Role
from app.models.tool_policy import TenantToolPolicy
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_recovery_service import AgentRecoveryService
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
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text="{}")


class _IdempotentCounterInput(BaseModel):
    pass


class _IdempotentCounterOutput(BaseModel):
    call_count: int
    logical_operations: int


class IdempotentCounterTool(Tool):
    """A fake external-side-effect tool, for concurrency testing only —
    never a real Stripe/Twilio/QuickBooks call (per this phase's own
    instructions). Simulates a provider with a genuine, verified
    idempotency-key mechanism: repeated calls with the SAME
    `context.idempotency_key` increment `call_count` (proving the tool
    really was invoked each time) but only ever perform ONE
    `logical_operations` increment per distinct key — exactly the shape of
    Stripe's own "same key -> cached original response" guarantee."""

    name = "test.idempotent_counter"
    description = "test-only fake external side effect with verified idempotency"
    input_schema = _IdempotentCounterInput
    output_schema = _IdempotentCounterOutput
    required_permission = None
    supports_idempotency = True

    def __init__(self) -> None:
        self.call_count = 0
        self._seen_keys: set[str] = set()
        self.logical_operations = 0
        self._lock = asyncio.Lock()

    async def execute(self, input: _IdempotentCounterInput, context: ExecutionContext) -> _IdempotentCounterOutput:
        async with self._lock:
            self.call_count += 1
            key = context.idempotency_key
            if key not in self._seen_keys:
                self._seen_keys.add(key)
                self.logical_operations += 1
            return _IdempotentCounterOutput(call_count=self.call_count, logical_operations=self.logical_operations)


async def _allow_auto_policy(tenant_id, tool_name: str) -> None:
    """Test-only: a tool unknown to DEFAULT_TOOL_POLICIES (app/tools/policy.py)
    defaults to APPROVAL_REQUIRED (deny-by-default) — PolicyService.set_policy
    itself refuses any tool_name it doesn't already recognize, so this
    inserts the TenantToolPolicy override row directly, exactly the shape
    set_policy would produce for a known tool, purely to let these
    concurrency tests exercise the AUTO/no-approval path for a fake tool
    that only ever exists in this test module."""
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
        tenant_id, name="PGSingleActionProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
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


async def _mid_flight_single_action_execution(tenant_id, agent_id, version_id, *, tool_name: str):
    """A SINGLE_ACTION execution whose durable step was left mid-flight by a
    (simulated) crash — `completed_at IS NULL`, exactly the shape
    `AgentExecutionService._create_step` leaves before the tool call."""
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent_id, agent_version_id=version_id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.SINGLE_ACTION,
            tool_name=tool_name, tool_input_summary={},
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.flush()
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution.id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
                tool_name=tool_name, started_at=datetime.now(timezone.utc), completed_at=None,
            )
        )
        await session.commit()
        await session.refresh(execution)
        return execution.id


@requires_real_postgres
async def test_five_way_recovery_race_exactly_one_worker_wins(tool_registry) -> None:
    """5 concurrent recovery workers race for the SAME stale SINGLE_ACTION
    execution — the atomic conditional UPDATE lease claim must let exactly
    one of them proceed to recovery, proven against real Postgres row
    locking, not SQLite."""
    tenant_id = uuid.uuid4()
    tool_registry.register(IdempotentCounterTool())
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.idempotent_counter")
    execution_id = await _mid_flight_single_action_execution(
        tenant_id, agent.id, version.id, tool_name="test.idempotent_counter"
    )

    workers = [_recovery(tool_registry, worker_id=uuid.uuid4()) for _ in range(_RACE_WIDTH)]
    results = await asyncio.gather(*(w.sweep_once(tenant_id) for w in workers))
    claimed_by = [r for r in results if execution_id in r]
    assert len(claimed_by) == 1  # exactly one of the 5 workers won

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.recovery_attempt_count == 1  # incremented exactly once, not 5 times
        assert execution.status == AgentExecutionStatus.COMPLETED


@requires_real_postgres
async def test_five_way_duplicate_execution_request_one_logical_execution(tool_registry) -> None:
    """5 concurrent `run_action` calls with the SAME idempotency_key for the
    same tenant+agent (e.g. 5 duplicate scheduler/event deliveries) must
    resolve to exactly one logical AgentExecution row — proven against real
    Postgres's unique-constraint + IntegrityError-recovery path (see
    AgentExecutionService._create_execution_row's Phase 6 bug-fix
    docstring), never a bare application-side existence check."""
    tenant_id = uuid.uuid4()
    tool_registry.register(IdempotentCounterTool())
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.idempotent_counter")
    key = f"dup-{uuid.uuid4()}"

    from app.services.agent_execution_service import DuplicateExecutionRequestError

    async def _attempt():
        service = _exec_service(tool_registry)
        try:
            execution = await service.run_action(
                tenant_id, agent.id, tool_name="test.idempotent_counter", tool_input={},
                triggered_by=None, idempotency_key=key,
            )
            return ("created", execution.id)
        except DuplicateExecutionRequestError as exc:
            return ("duplicate", exc.existing_execution_id)

    results = await asyncio.gather(*(_attempt() for _ in range(_RACE_WIDTH)))
    created = [r for r in results if r[0] == "created"]
    assert len(created) == 1  # exactly one of the 5 concurrent callers actually created the row

    async with async_session_maker() as session:
        from sqlalchemy import func, select

        count = (
            await session.execute(
                select(func.count(AgentExecution.id)).where(
                    AgentExecution.tenant_id == tenant_id, AgentExecution.idempotency_key == key,
                )
            )
        ).scalar_one()
        assert count == 1  # never two rows for the same idempotency key, even under a 5-way race


@requires_real_postgres
async def test_five_way_concurrent_idempotent_tool_calls_one_logical_side_effect(tool_registry) -> None:
    """5 concurrent recovery attempts at retrying the SAME mid-flight step
    for a tool with verified `supports_idempotency=True` must all reuse the
    identical deterministic idempotency identity — the fake provider proves
    it sees 5 real invocations (`call_count`) but only 1 distinct logical
    operation (`logical_operations`), exactly the "at most one logical side
    effect" guarantee this phase requires before claiming exactly-once for
    any tool."""
    tool = IdempotentCounterTool()
    tool_registry.register(tool)
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.idempotent_counter")
    execution_id = await _mid_flight_single_action_execution(
        tenant_id, agent.id, version.id, tool_name="test.idempotent_counter"
    )

    # 5 concurrent, independent resume_recovered calls for the SAME
    # execution (simulating 5 racing recovery attempts that all believe
    # they should retry this step) — the deterministic identity formula
    # (execution_id + step_number) means every one of them computes the
    # identical key, with no coordination needed.
    services = [_exec_service(tool_registry, worker_id=uuid.uuid4()) for _ in range(_RACE_WIDTH)]
    await asyncio.gather(*(s.resume_recovered(tenant_id, execution_id) for s in services))

    assert tool.call_count == _RACE_WIDTH  # the tool really was invoked 5 times
    assert tool.logical_operations == 1  # but resolved to exactly one logical side effect

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED


@requires_real_postgres
async def test_ambiguous_non_idempotent_tool_never_reexecuted_under_concurrency(tool_registry) -> None:
    """The negative control for the test above: a tool WITHOUT verified
    idempotency support must never be retried by recovery, even once, let
    alone under concurrency — every one of 5 concurrent resume_recovered
    calls for the same mid-flight step must safe-halt, none may call the
    tool."""
    tool = IdempotentCounterTool()
    tool.supports_idempotency = False  # explicit negative control
    tool_registry.register(tool)
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.idempotent_counter")
    execution_id = await _mid_flight_single_action_execution(
        tenant_id, agent.id, version.id, tool_name="test.idempotent_counter"
    )

    services = [_exec_service(tool_registry, worker_id=uuid.uuid4()) for _ in range(_RACE_WIDTH)]
    await asyncio.gather(*(s.resume_recovered(tenant_id, execution_id) for s in services))

    assert tool.call_count == 0  # never invoked — safe-halted every time

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.HALTED
        assert execution.termination_reason == AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT


# --------------------------------------------------------------------------
# Phase 8: the COMBINED lease + idempotency adversarial interaction. Phase 7
# proved execution-level lease serialization (test 1 above) and tool-level
# idempotency (tests 3/4 above) SEPARATELY. These two tests exercise the
# full production call path — `AgentRecoveryService.sweep_once`, which
# claims the LEASE first and only then calls resume_recovered — under a
# genuine adversarial timing: Worker A's lease is already expired (it
# "died" mid-flight, after starting the tool), and multiple Worker B's race
# to both claim the lease AND retry the tool, all through the one public
# entry point production code actually calls.
# --------------------------------------------------------------------------


@requires_real_postgres
async def test_combined_lease_and_idempotency_race_idempotent_tool_one_logical_effect(tool_registry) -> None:
    """Worker A acquires the lease, persists the step, starts an idempotent
    tool call, then its lease expires (simulated crash). 5 concurrent
    Worker B's call `sweep_once` (the real production entry point — lease
    claim THEN resume, not resume_recovered in isolation) racing for the
    same execution. Exactly one must win the lease claim (Phase 6/7's own
    proof, re-verified here under the combined path), and the tool's own
    idempotency mechanism must resolve to exactly one logical side effect
    even though the lease-claim race and the tool-retry race are now
    happening together, not in isolation."""
    tool = IdempotentCounterTool()
    tool_registry.register(tool)
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.idempotent_counter")
    execution_id = await _mid_flight_single_action_execution(
        tenant_id, agent.id, version.id, tool_name="test.idempotent_counter"
    )

    workers = [_recovery(tool_registry, worker_id=uuid.uuid4()) for _ in range(_RACE_WIDTH)]
    results = await asyncio.gather(*(w.sweep_once(tenant_id) for w in workers))
    claimed_by = [r for r in results if execution_id in r]
    assert len(claimed_by) == 1  # the LEASE race still has exactly one winner under the combined path
    assert tool.logical_operations == 1  # and that one winner's idempotent retry is exactly-once logically

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED
        assert execution.recovery_attempt_count == 1


@requires_real_postgres
async def test_combined_lease_and_idempotency_race_non_idempotent_tool_safe_halts(tool_registry) -> None:
    """The negative-control mirror of the test above, through the same
    combined `sweep_once` path: a non-idempotent tool's mid-flight step,
    raced by 5 concurrent recovery workers. Exactly one worker wins the
    lease (same proof as above), and that winner must safe-halt — the tool
    must never be invoked, not even once, regardless of how the lease race
    and the tool-retry decision interact."""
    tool = IdempotentCounterTool()
    tool.supports_idempotency = False
    tool_registry.register(tool)
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id, extra_tool="test.idempotent_counter")
    execution_id = await _mid_flight_single_action_execution(
        tenant_id, agent.id, version.id, tool_name="test.idempotent_counter"
    )

    workers = [_recovery(tool_registry, worker_id=uuid.uuid4()) for _ in range(_RACE_WIDTH)]
    results = await asyncio.gather(*(w.sweep_once(tenant_id) for w in workers))
    claimed_by = [r for r in results if execution_id in r]
    assert len(claimed_by) == 1  # lease race still has exactly one winner
    assert tool.call_count == 0  # and that winner never invoked the non-idempotent tool

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.HALTED
        assert execution.termination_reason == AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT
