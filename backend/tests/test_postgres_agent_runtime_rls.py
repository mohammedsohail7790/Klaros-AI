"""Phase 4 (KLAROS_FINAL_SECURITY_MODEL.md §D "new tables RLS-on-day-one"):
RLS audit-mode instrumentation on the four new Agent Runtime tables, plus
real-Postgres-only constraint verification (unique version per agent,
unique tool-permission per agent, unique idempotency key per tenant+agent,
FK behavior). Mirrors tests/test_postgres_recommendation_rls_audit_mode.py's
structure exactly. Skipped entirely unless DATABASE_URL points at a real
PostgreSQL instance.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import async_session_maker, engine

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

TABLES = ("agents", "agent_versions", "agent_tool_permissions", "agent_executions")
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0045's RLS DDL
    never applies there — re-apply it here, same rationale as every other
    Phase 0-3 `test_postgres_*_rls_audit_mode.py` fixture."""
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
        assert forced.scalar() is False, "audit-mode only — FORCE is a separate, later, whole-system decision"

        policy_count = await session.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
        )
        assert policy_count.scalar() == 1


@requires_real_postgres
async def test_audit_mode_is_a_real_no_op_today() -> None:
    """A context-less session (no set_tenant_context call) must still see
    every row — genuinely audit-mode, not enforcement, exactly as the rest
    of this codebase's RLS rollout is today."""
    from app.models.agent import Agent, AgentAutonomyTier, AgentStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(
            Agent(
                tenant_id=tenant_id, name="RLS Probe", purpose="x", status=AgentStatus.DRAFT,
                autonomy_tier=AgentAutonomyTier.OBSERVE, acting_role="MANAGER",
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT tenant_id FROM agents WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )
        assert result.first() is not None


@requires_real_postgres
async def test_explicit_application_level_tenant_filtering_is_still_required() -> None:
    """RLS is audit-mode only (no enforcement) — proves the application's
    own tenant_id filtering (not RLS) is what actually isolates tenants
    today, exercised through AgentService against real Postgres."""
    from app.models.rbac import Role
    from app.services.agent_service import AgentNotFoundError, AgentService

    service = AgentService(async_session_maker)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent = await service.create_agent(
        tenant_a, name="A", purpose="x", autonomy_tier="OBSERVE", acting_role=Role.MANAGER, created_by=None
    )
    with pytest.raises(AgentNotFoundError):
        await service.get_agent(tenant_b, agent.id)
    got = await service.get_agent(tenant_a, agent.id)
    assert got.id == agent.id


@requires_real_postgres
async def test_unique_version_per_agent_enforced_at_db_level() -> None:
    from app.models.agent import Agent, AgentAutonomyTier, AgentStatus, AgentVersion, AgentVersionStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        agent = Agent(
            tenant_id=tenant_id, name="Dup Version", purpose="x", status=AgentStatus.DRAFT,
            autonomy_tier=AgentAutonomyTier.OBSERVE, acting_role="MANAGER",
        )
        session.add(agent)
        await session.flush()
        session.add(
            AgentVersion(
                tenant_id=tenant_id, agent_id=agent.id, version=1, status=AgentVersionStatus.DRAFT,
                instructions_snapshot="x", tool_permissions_snapshot=[], memory_refs=[], triggers={},
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        session.add(
            AgentVersion(
                tenant_id=tenant_id, agent_id=agent.id, version=1, status=AgentVersionStatus.DRAFT,
                instructions_snapshot="dup", tool_permissions_snapshot=[], memory_refs=[], triggers={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@requires_real_postgres
async def test_unique_tool_permission_per_agent_enforced_at_db_level() -> None:
    from app.models.agent import Agent, AgentAutonomyTier, AgentStatus, AgentToolPermission

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        agent = Agent(
            tenant_id=tenant_id, name="Dup Grant", purpose="x", status=AgentStatus.DRAFT,
            autonomy_tier=AgentAutonomyTier.OBSERVE, acting_role="MANAGER",
        )
        session.add(agent)
        await session.flush()
        session.add(
            AgentToolPermission(tenant_id=tenant_id, agent_id=agent.id, tool_name="system.get_tenant_context")
        )
        await session.commit()

    async with async_session_maker() as session:
        session.add(
            AgentToolPermission(tenant_id=tenant_id, agent_id=agent.id, tool_name="system.get_tenant_context")
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@requires_real_postgres
async def test_idempotency_key_uniqueness_is_per_tenant_agent_and_nulls_are_distinct() -> None:
    """The real-Postgres-only behavior SQLite would hide: two NULL
    idempotency_key rows for the same tenant+agent must NOT collide (NULL
    is never equal to itself in a unique index), while two equal,
    non-NULL keys for the same tenant+agent must collide."""
    from app.models.agent import (
        Agent, AgentAutonomyTier, AgentExecution, AgentExecutionStatus, AgentStatus,
        AgentVersion, AgentVersionStatus,
    )

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        agent = Agent(
            tenant_id=tenant_id, name="Idem", purpose="x", status=AgentStatus.DRAFT,
            autonomy_tier=AgentAutonomyTier.OBSERVE, acting_role="MANAGER",
        )
        session.add(agent)
        await session.flush()
        version = AgentVersion(
            tenant_id=tenant_id, agent_id=agent.id, version=1, status=AgentVersionStatus.PUBLISHED,
            instructions_snapshot="x", tool_permissions_snapshot=[], memory_refs=[], triggers={},
        )
        session.add(version)
        await session.flush()
        # Two NULL-idempotency-key rows: must NOT collide.
        session.add(
            AgentExecution(
                tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
                status=AgentExecutionStatus.COMPLETED, tool_name="system.get_tenant_context",
                tool_input_summary={}, idempotency_key=None,
            )
        )
        session.add(
            AgentExecution(
                tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
                status=AgentExecutionStatus.COMPLETED, tool_name="system.get_tenant_context",
                tool_input_summary={}, idempotency_key=None,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        session.add(
            AgentExecution(
                tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
                status=AgentExecutionStatus.COMPLETED, tool_name="system.get_tenant_context",
                tool_input_summary={}, idempotency_key="dup-key",
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        session.add(
            AgentExecution(
                tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
                status=AgentExecutionStatus.COMPLETED, tool_name="system.get_tenant_context",
                tool_input_summary={}, idempotency_key="dup-key",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@requires_real_postgres
async def test_full_governed_execution_pipeline_against_real_postgres() -> None:
    """End-to-end against real PostgreSQL: create -> grant -> version ->
    publish -> activate -> execute, through the real ToolRegistry (not a
    stub), verifying the JSON snapshot column round-trips correctly under
    asyncpg."""
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport
    from app.models.agent import AgentExecutionStatus
    from app.models.rbac import Role
    from app.services.agent_execution_service import AgentExecutionService
    from app.services.agent_service import AgentService
    from app.tools.factory import build_tool_registry

    tenant_id = uuid.uuid4()
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    tool_registry = build_tool_registry(async_session_maker, bus)
    agent_service = AgentService(async_session_maker)
    execution_service = AgentExecutionService(async_session_maker, tool_registry)

    agent = await agent_service.create_agent(
        tenant_id, name="PG E2E", purpose="x", autonomy_tier="EXECUTE_AUTONOMOUS",
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.COMPLETED
