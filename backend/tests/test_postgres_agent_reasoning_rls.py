"""Phase 5 (bounded LLM-driven reasoning loop): RLS audit-mode
instrumentation on the new `agent_execution_steps` table, plus
real-Postgres-only constraint verification (unique (execution_id,
step_number), nullable `agent_executions.tool_name` for REASONING mode)
and one full governed reasoning pipeline run end to end against a real
PostgreSQL instance. Mirrors tests/test_postgres_agent_runtime_rls.py's
structure exactly. Skipped entirely unless DATABASE_URL points at a real
PostgreSQL instance.
"""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import async_session_maker, engine
from app.models.agent import AgentExecutionStatus
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_service import AgentService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

TABLES = ("agent_execution_steps",)
POLICY_NAME = "tenant_isolation_audit_policy"


class _FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, responses):
        self._responses = list(responses)

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        item = self._responses.pop(0)
        text_ = item if isinstance(item, str) else json.dumps(item)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text_)


def _decision(action, **kwargs):
    d = {"action": action, "reasoning_summary": "r", "arguments": {}}
    d.update(kwargs)
    return d


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0046's RLS DDL
    never applies there — re-apply it here, same rationale as every other
    Phase 0-4 `test_postgres_*_rls_audit_mode.py` fixture."""
    if "postgresql" not in _settings.DATABASE_URL:
        yield
        return
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}"))
            await conn.execute(
                text(f"CREATE POLICY {POLICY_NAME} ON {table} FOR ALL USING (true) WITH CHECK (true)")
            )
    yield


@requires_real_postgres
@pytest.mark.parametrize("table", TABLES)
async def test_rls_is_enabled_audit_mode_only(table: str) -> None:
    async with async_session_maker() as session:
        enabled = await session.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
        )
        assert enabled.scalar() is True
        forced = await session.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
        )
        assert forced.scalar() is False
        policy_count = await session.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
        )
        assert policy_count.scalar() == 1


@requires_real_postgres
async def test_agent_executions_tool_name_is_nullable_for_reasoning_mode() -> None:
    """Real-Postgres-only: a REASONING execution row with tool_name=NULL
    must actually INSERT cleanly — SQLite's looser NOT NULL enforcement
    (and the ORM-level metadata build tests use) could otherwise hide a
    real constraint mismatch."""
    from app.models.agent import Agent, AgentAutonomyTier, AgentExecution, AgentExecutionMode, AgentStatus, AgentVersion, AgentVersionStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        agent = Agent(
            tenant_id=tenant_id, name="PG Reasoning Probe", purpose="x", status=AgentStatus.ACTIVE,
            autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS, acting_role="MANAGER",
        )
        session.add(agent)
        await session.flush()
        version = AgentVersion(
            tenant_id=tenant_id, agent_id=agent.id, version=1, status=AgentVersionStatus.PUBLISHED,
            instructions_snapshot="x", tool_permissions_snapshot=[], memory_refs=[], triggers={},
        )
        session.add(version)
        await session.flush()
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.PENDING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="probe",
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        assert execution.tool_name is None


@requires_real_postgres
async def test_unique_execution_step_number_enforced_at_db_level() -> None:
    """Real-Postgres-only: proves BOTH the unique (execution_id,
    step_number) constraint AND the execution_id -> agent_executions FK
    are genuinely enforced at the database level (not just app-level
    convention) — a real FK-violation bug was caught writing this test
    (see PHASE_5_IMPLEMENTATION_LOG.md): an execution_id with no matching
    `agent_executions` row is correctly rejected by Postgres, which
    SQLite's default (non-`PRAGMA foreign_keys=ON`) behavior would have
    silently allowed."""
    from app.models.agent import (
        Agent, AgentAutonomyTier, AgentExecution, AgentExecutionMode, AgentExecutionStep,
        AgentExecutionStepStatus, AgentExecutionStepType, AgentStatus, AgentVersion, AgentVersionStatus,
    )

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        agent = Agent(
            tenant_id=tenant_id, name="Unique Step Probe", purpose="x", status=AgentStatus.ACTIVE,
            autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS, acting_role="MANAGER",
        )
        session.add(agent)
        await session.flush()
        version = AgentVersion(
            tenant_id=tenant_id, agent_id=agent.id, version=1, status=AgentVersionStatus.PUBLISHED,
            instructions_snapshot="x", tool_permissions_snapshot=[], memory_refs=[], triggers={},
        )
        session.add(version)
        await session.flush()
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="probe",
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    async with async_session_maker() as session:
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution_id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        session.add(
            AgentExecutionStep(
                tenant_id=tenant_id, execution_id=execution_id, step_number=1,
                step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@requires_real_postgres
async def test_full_governed_reasoning_pipeline_against_real_postgres(tool_registry) -> None:
    """End-to-end: create -> grant -> version(max_tool_chain_depth=3) ->
    publish -> activate -> reasoning run (2 tool calls + COMPLETE) against
    the real PostgreSQL instance this test session is using — proves JSON/
    JSONB columns (reasoning_state, tool_permissions_snapshot,
    input_summary/output_summary), FK behavior, and transaction ordering
    all work for real, not just against SQLite."""
    from app.db.session import async_session_maker as asm

    agent_service = AgentService(asm)
    execution_service = AgentExecutionService(asm, tool_registry)
    tenant_id = uuid.uuid4()

    agent = await agent_service.create_agent(
        tenant_id, name="PG Full Reasoning", purpose="x", autonomy_tier="EXECUTE_AUTONOMOUS",
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_current_time", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x", max_tool_chain_depth=3, created_by=None
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)

    provider = _FakeAIProvider([
        _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
        _decision("TOOL_CALL", tool_name="system.get_current_time"),
        _decision("COMPLETE", final_response="pg pipeline done"),
    ])
    rs = AgentReasoningService(asm, tool_registry, execution_service, ai_provider=provider)
    execution = await rs.start(tenant_id, agent.id, goal="pg probe", triggered_by=None)

    assert execution.status == AgentExecutionStatus.COMPLETED
    assert execution.final_response == "pg pipeline done"
    steps = await rs.list_steps(tenant_id, execution.id)
    assert len(steps) == 3
    assert steps[0].output_summary["tenant_id"] == str(tenant_id)
