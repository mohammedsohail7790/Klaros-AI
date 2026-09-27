"""Phase 1 (KLAROS_FINAL_SECURITY_MODEL.md §D "new tables RLS-on-day-one"):
RLS audit-mode instrumentation on `organization_vertical_extensions`, the
one genuinely tenant-scoped table Phase 1 adds (`vertical_extensions` and
`integration_provider_catalog` are intentionally global reference data —
see their own model docstrings — and correctly get NO RLS policy at all).

Mirrors tests/test_postgres_rls_audit_mode.py's structure exactly, scoped
to the one new table, at the same audit-mode maturity level (not
enforcement) Phase 0 established. Skipped entirely unless DATABASE_URL
points at a real PostgreSQL instance.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, engine

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

TABLE = "organization_vertical_extensions"
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0041's RLS DDL
    never applies there — re-apply it here, same rationale as
    test_postgres_rls_audit_mode.py's identical fixture."""
    if "postgresql" not in _settings.DATABASE_URL:
        yield
        return
    async with engine.begin() as conn:
        await conn.execute(text(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {TABLE}"))
        await conn.execute(
            text(f"CREATE POLICY {POLICY_NAME} ON {TABLE} FOR ALL USING (true) WITH CHECK (true)")
        )
    yield


async def _seed_vertical(session) -> uuid.UUID:
    from app.models.vertical_extension import VerticalExtension

    vertical = VerticalExtension(key=f"test-vertical-{uuid.uuid4()}", name="Test Vertical")
    session.add(vertical)
    await session.commit()
    await session.refresh(vertical)
    return vertical.id


@requires_real_postgres
async def test_rls_is_enabled_on_organization_vertical_extensions() -> None:
    async with async_session_maker() as session:
        enabled = await session.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": TABLE}
        )
        assert enabled.scalar() is True

        forced = await session.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": TABLE}
        )
        assert forced.scalar() is False, "audit-mode only — FORCE is a separate, later, whole-system decision"

        policy_count = await session.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": TABLE}
        )
        assert policy_count.scalar() == 1


@requires_real_postgres
async def test_audit_mode_is_a_real_no_op_today() -> None:
    from app.models.vertical_extension import OrganizationVerticalExtension
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        vertical_id = await _seed_vertical(session)
        session.add(
            OrganizationVerticalExtension(
                tenant_id=tenant_id, vertical_extension_id=vertical_id, enabled_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        # Deliberately do NOT call set_tenant_context.
        result = await session.execute(
            text(f"SELECT tenant_id FROM {TABLE} WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )
        row = result.first()
        assert row is not None, "audit-mode policy must not hide rows from a context-less session"


@requires_real_postgres
async def test_vertical_extensions_and_catalog_have_no_rls_policy() -> None:
    """The two intentionally-global tables must NOT get a tenant RLS
    policy — there is no tenant_id column to filter on, and applying one
    would be a no-op at best, a broken migration at worst."""
    async with async_session_maker() as session:
        for table in ("vertical_extensions", "integration_provider_catalog"):
            policy_count = await session.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
            assert policy_count.scalar() == 0, f"{table} is global reference data and must not carry a tenant RLS policy"
