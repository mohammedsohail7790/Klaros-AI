"""Phase 9 (MCP server exposure): RLS audit-mode instrumentation on the two
new tables (`mcp_tool_exposures`, `mcp_client_credentials`), plus
real-Postgres-only constraint verification (unique tool exposure per
tenant, unique credential token hash). Mirrors
tests/test_postgres_agent_runtime_rls.py's structure exactly. Skipped
entirely unless DATABASE_URL points at a real PostgreSQL instance.
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

TABLES = ("mcp_tool_exposures", "mcp_client_credentials")
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
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
async def test_duplicate_tool_exposure_for_same_tenant_rejected() -> None:
    from app.models.mcp_server import McpToolExposure

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(McpToolExposure(tenant_id=tenant_id, tool_name="crm.create_lead", enabled=True))
        await session.commit()

    async with async_session_maker() as session:
        session.add(McpToolExposure(tenant_id=tenant_id, tool_name="crm.create_lead", enabled=False))
        with pytest.raises(IntegrityError):
            await session.commit()


@requires_real_postgres
async def test_same_tool_name_can_be_exposed_independently_per_tenant() -> None:
    """The uniqueness constraint is (tenant_id, tool_name) — two different
    tenants exposing the same tool name must not collide."""
    from app.models.mcp_server import McpToolExposure

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add(McpToolExposure(tenant_id=tenant_a, tool_name="crm.create_lead", enabled=True))
        session.add(McpToolExposure(tenant_id=tenant_b, tool_name="crm.create_lead", enabled=True))
        await session.commit()


@requires_real_postgres
async def test_duplicate_token_hash_globally_rejected() -> None:
    """`token_hash` is unique across ALL tenants, not per-tenant — a real
    SHA-256 collision is cryptographically implausible, so a duplicate here
    means a genuine token-reuse bug, and the DB must refuse it outright."""
    from app.models.mcp_server import McpClientCredential, McpCredentialStatus

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    shared_hash = uuid.uuid4().hex + uuid.uuid4().hex[:32]
    async with async_session_maker() as session:
        session.add(
            McpClientCredential(
                tenant_id=tenant_a, name="a", role="OWNER", token_hash=shared_hash, token_prefix="mcpkl_a",
                status=McpCredentialStatus.ACTIVE,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        session.add(
            McpClientCredential(
                tenant_id=tenant_b, name="b", role="OWNER", token_hash=shared_hash, token_prefix="mcpkl_b",
                status=McpCredentialStatus.ACTIVE,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@requires_real_postgres
async def test_audit_mode_is_a_real_no_op_today() -> None:
    """A context-less session (no set_tenant_context call) must still see
    every row — genuinely audit-mode, not enforcement, matching the rest of
    this codebase's RLS rollout — never claim enforcement this phase does
    not implement."""
    from app.models.mcp_server import McpToolExposure

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(McpToolExposure(tenant_id=tenant_id, tool_name="crm.create_lead", enabled=True))
        await session.commit()

    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT tenant_id FROM mcp_tool_exposures WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )
        assert result.first() is not None
