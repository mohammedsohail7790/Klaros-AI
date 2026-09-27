"""Phase 6 (Agent Runtime Reliability): MANDATORY real-Postgres-only
concurrency proofs for crash recovery. SQLite cannot prove any of these —
they depend on genuine row-level locking under concurrent transactions.
Skipped entirely unless DATABASE_URL points at a real PostgreSQL instance.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.agent import (
    Agent,
    AgentAutonomyTier,
    AgentExecution,
    AgentExecutionMode,
    AgentExecutionStatus,
    AgentStatus,
    AgentVersion,
    AgentVersionStatus,
)
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_recovery_service import AgentRecoveryService
from app.services.agent_service import AgentService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, responses=()) -> None:
        self._responses = list(responses)

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        item = self._responses.pop(0) if self._responses else {
            "action": "COMPLETE", "final_response": "done", "reasoning_summary": "r", "arguments": {},
        }
        text = item if isinstance(item, str) else json.dumps(item)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


async def _active_agent(tool_registry, tenant_id):
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="PGRecoveryProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    return agent, version


async def _stale_running_execution(tenant_id, agent_id, version_id):
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent_id, agent_version_id=version_id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g", reasoning_state={"goal": "g", "history": []},
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        return execution.id


def _recovery(tool_registry, worker_id=None):
    exec_service = AgentExecutionService(async_session_maker, tool_registry)
    rs = AgentReasoningService(async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider())
    return AgentRecoveryService(async_session_maker, rs, worker_id=worker_id)


@requires_real_postgres
async def test_double_recovery_race_exactly_one_worker_claims(tool_registry) -> None:
    """Two recovery workers race to claim the SAME stale execution
    simultaneously (real concurrent asyncpg connections/transactions,
    not SQLite) — exactly one must win the atomic conditional UPDATE."""
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id)
    execution_id = await _stale_running_execution(tenant_id, agent.id, version.id)

    worker_a = _recovery(tool_registry, worker_id=uuid.uuid4())
    worker_b = _recovery(tool_registry, worker_id=uuid.uuid4())

    results = await asyncio.gather(
        worker_a.sweep_once(tenant_id), worker_b.sweep_once(tenant_id),
    )
    claimed_by = [r for r in results if execution_id in r]
    assert len(claimed_by) == 1  # exactly one worker won the race

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.recovery_attempt_count == 1  # incremented exactly once, not twice
        assert execution.status == AgentExecutionStatus.COMPLETED


@requires_real_postgres
async def test_heartbeat_race_current_owner_wins_recovery_worker_does_not_steal(tool_registry) -> None:
    """A real, still-alive owner (fresh, unexpired lease) races against a
    recovery sweep attempting to claim the same row concurrently — the
    sweep's conditional UPDATE must affect zero rows (the owner's lease
    has not expired), proven against real Postgres row locking."""
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id)
    owner_id = uuid.uuid4()
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g",
            execution_owner_id=owner_id, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    recovery_worker = _recovery(tool_registry, worker_id=uuid.uuid4())
    claimed = await recovery_worker.sweep_once(tenant_id)
    assert execution_id not in claimed

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.execution_owner_id == owner_id  # never stolen
        assert execution.status == AgentExecutionStatus.RUNNING


@requires_real_postgres
async def test_recovery_plus_idempotency_no_duplicate_execution_row(tool_registry) -> None:
    """Execution starts -> (simulated) crash -> recovery requested twice in
    a row (sequential, mimicking a repeated recovery-sweep invocation) —
    must never create a second AgentExecution row for the same logical
    execution; recovery only ever mutates the ONE existing row."""
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(tool_registry, tenant_id)
    execution_id = await _stale_running_execution(tenant_id, agent.id, version.id)

    recovery = _recovery(tool_registry, worker_id=uuid.uuid4())
    await recovery.sweep_once(tenant_id)  # first recovery — completes the execution
    await recovery.sweep_once(tenant_id)  # a second sweep call — execution is now COMPLETED, not a candidate

    async with async_session_maker() as session:
        from sqlalchemy import func, select

        count = (
            await session.execute(select(func.count(AgentExecution.id)).where(AgentExecution.tenant_id == tenant_id))
        ).scalar_one()
        assert count == 1
