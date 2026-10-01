"""Phase 17B-1: real-PostgreSQL validation of the restricted runtime
application database role (backend/scripts/db/provision_app_role.py).

Context (see PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md §7 and
PHASE_17B1_RESTRICTED_DB_ROLE_IMPLEMENTATION_LOG.md): every environment
today, including this test suite's own `DATABASE_URL`, connects as the
Postgres cluster's bootstrap/initdb role, which is always a superuser and
owns every table. This file proves the NEW, separate, restricted role
this phase introduces is actually usable for the application's normal
DML traffic while genuinely lacking superuser/owner/BYPASSRLS power —
without requiring `DATABASE_URL` itself to be repointed at that role for
the rest of this test session (most of this suite's ~1800 other
real-Postgres tests manage their own schema via `Base.metadata.create_all`
in `conftest.py`'s autouse `_reset_database` fixture, which is itself an
owner-level operation incompatible with a non-owning connection — so this
file creates its OWN short-lived app role and its OWN separate engine
pointed at it, rather than mutating global settings/DATABASE_URL).

This is a different, complementary test from
`backend/tests/test_postgres_rls_audit_mode.py`'s
`test_enforcing_policy_fails_safe_with_no_tenant_context`, which proves
the RLS *policy mechanism* works against a purpose-built, throwaway,
non-privileged *probe* role (`rls_probe`-style) inside a rolled-back
transaction. This file instead proves the actual, real, persistent
*runtime application role* this phase provisions
(`backend/scripts/db/provision_app_role.py`) is correctly
non-superuser/non-owner/non-bypassrls and can perform the DML the
application genuinely needs — see PHASE_17B1's own report for why the two
roles are deliberately kept conceptually separate.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import engine as owner_engine
from scripts.db.provision_app_role import provision_app_role

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _app_role_url(app_user: str, app_password: str) -> str:
    """Build an app-role connection URL against the SAME host/db this test
    session's owner engine already targets (works identically against
    Docker Compose Postgres, CI's Postgres service container, or a
    pgserver-backed disposable instance — never hardcodes a host)."""
    owner_url = owner_engine.url
    return str(
        owner_url.set(username=app_user, password=app_password, drivername="postgresql+asyncpg")
    )


@pytest_asyncio.fixture
async def app_role():
    """Provisions a real, throwaway restricted application role for the
    duration of one test, using the exact same provisioning function
    (`provision_app_role`) that dev/CI/staging/production are meant to run
    — i.e. this is not a hand-rolled substitute for the real mechanism,
    it IS the real mechanism, exercised end-to-end against real
    PostgreSQL. Torn down in a `finally` block regardless of test outcome
    so a failed assertion never leaves a role behind for the next test."""
    app_user = f"phase17b1_app_{uuid.uuid4().hex[:10]}"
    app_password = uuid.uuid4().hex

    await provision_app_role(
        owner_dsn=str(owner_engine.url).replace("postgresql+asyncpg", "postgresql"),
        app_user=app_user,
        app_password=app_password,
    )

    app_engine = create_async_engine(_app_role_url(app_user, app_password), pool_pre_ping=True)
    try:
        yield app_user, app_engine
    finally:
        await app_engine.dispose()
        async with owner_engine.begin() as conn:
            await conn.execute(text(f'REASSIGN OWNED BY "{app_user}" TO CURRENT_USER'))
            await conn.execute(text(f'DROP OWNED BY "{app_user}"'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{app_user}"'))


@requires_real_postgres
async def test_app_role_is_not_superuser_or_bypassrls(app_role) -> None:
    app_user, app_engine = app_role
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolreplication "
                    "FROM pg_roles WHERE rolname = :name"
                ),
                {"name": app_user},
            )
        ).one()
        rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolreplication = row
        assert rolsuper is False, "restricted app role must NOT be SUPERUSER"
        assert rolbypassrls is False, "restricted app role must NOT be BYPASSRLS"
        assert rolcanlogin is True, "restricted app role must be able to LOGIN"
        assert rolcreatedb is False, "restricted app role must NOT be CREATEDB"
        assert rolcreaterole is False, "restricted app role must NOT be CREATEROLE"
        assert rolreplication is False, "restricted app role must NOT be REPLICATION"


@requires_real_postgres
async def test_app_role_does_not_own_application_tables(app_role) -> None:
    app_user, app_engine = app_role
    async with owner_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT tablename, tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tableowner = :owner"
            ),
            {"owner": app_user},
        )
        owned = result.all()
        assert owned == [], f"restricted app role must not own any table, but owns: {owned}"


@requires_real_postgres
async def test_app_role_can_select_insert_update_delete(app_role) -> None:
    """The core positive case: ordinary application DML must work
    end-to-end through the restricted role's own connection, on a
    representative table from the Tier-1 RLS-audit-mode set (§12 of
    PHASE_17A) — proving audit-mode's `USING (true)` policy still admits
    normal traffic under the new role, exactly as it does under the
    current owner/superuser connection."""
    app_user, app_engine = app_role
    tenant_id = uuid.uuid4()
    row_id = uuid.uuid4()
    key = f"phase17b1-{uuid.uuid4()}"

    async with app_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO company_memories "
                "(id, tenant_id, key, value, memory_type, status, source, created_at, updated_at) "
                "VALUES (:id, :tenant_id, :key, 'phase17b1 role-cutover test', 'BUSINESS_RULE', "
                "'ACTIVE', 'OWNER_EXPLICIT', now(), now())"
            ),
            {"id": row_id, "tenant_id": tenant_id, "key": key},
        )

    async with app_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT value FROM company_memories WHERE id = :id"), {"id": row_id}
        )
        assert result.scalar() == "phase17b1 role-cutover test"

    async with app_engine.begin() as conn:
        await conn.execute(
            text("UPDATE company_memories SET value = 'updated by app role' WHERE id = :id"),
            {"id": row_id},
        )
    async with app_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT value FROM company_memories WHERE id = :id"), {"id": row_id}
        )
        assert result.scalar() == "updated by app role"

    async with app_engine.begin() as conn:
        await conn.execute(text("DELETE FROM company_memories WHERE id = :id"), {"id": row_id})
    async with app_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT count(*) FROM company_memories WHERE id = :id"), {"id": row_id}
        )
        assert result.scalar() == 0


@requires_real_postgres
async def test_app_role_can_select_a_currently_unprotected_tenant_table(app_role) -> None:
    """Sanity check on one of PHASE_17A's 100 currently-un-instrumented
    tenant tables (no RLS at all — see that report's §4): the restricted
    role must still be able to read/write it normally, proving this
    phase's grants are not accidentally scoped only to the 32
    RLS-audit-mode tables."""
    app_user, app_engine = app_role
    async with app_engine.connect() as conn:
        # A plain, harmless read against a table with no RLS today —
        # organizations is a good target: no tenant_id (it IS the tenant
        # root) so this can't collide with any other test's data by key.
        result = await conn.execute(text("SELECT count(*) FROM organizations"))
        assert result.scalar() is not None


@requires_real_postgres
async def test_app_role_cannot_create_alter_or_drop_tables(app_role) -> None:
    """Negative security test (§14): the restricted role must not be able
    to perform schema DDL — it is not the table owner and has no CREATE
    privilege on the schema."""
    app_user, app_engine = app_role

    async with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            async with conn.begin():
                await conn.execute(text("CREATE TABLE phase17b1_should_fail (id int)"))

    async with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            async with conn.begin():
                await conn.execute(text("ALTER TABLE company_memories ADD COLUMN phase17b1_should_fail int"))

    async with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            async with conn.begin():
                await conn.execute(text("DROP TABLE company_memories"))


@requires_real_postgres
async def test_app_role_cannot_create_roles_or_elevate_itself(app_role) -> None:
    """Negative security test (§14): the restricted role must not be able
    to create new roles, grant itself superuser, or otherwise escalate."""
    app_user, app_engine = app_role

    async with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            async with conn.begin():
                await conn.execute(text("CREATE ROLE phase17b1_should_fail LOGIN"))

    async with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            async with conn.begin():
                await conn.execute(text(f'ALTER ROLE "{app_user}" SUPERUSER'))


@requires_real_postgres
async def test_app_role_current_user_and_session_user_are_the_restricted_role(app_role) -> None:
    """The application must never silently be operating as a different,
    more-privileged identity than the connection string says — confirms
    both `current_user` and `session_user` genuinely report the restricted
    role, not some default/owner fallback."""
    app_user, app_engine = app_role
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT current_user, session_user"))
        current_user, session_user = result.one()
        assert current_user == app_user
        assert session_user == app_user


@requires_real_postgres
async def test_app_role_pooled_tenant_context_does_not_leak(app_role) -> None:
    """Re-runs the same pool-safety property
    `test_postgres_rls_audit_mode.py::test_set_local_tenant_context_does_not_leak_across_pooled_connections`
    already proves for the owner-role connection (§16 of
    PHASE_17B1_RESTRICTED_DB_ROLE_IMPLEMENTATION_LOG.md) — same semantics,
    same assertions, run instead through the new restricted role's own
    pooled engine, confirming the role cutover does not alter this
    already-proven-safe connection/session behavior."""
    app_user, app_engine = app_role
    from app.db.session import set_tenant_context

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    app_session_maker = owner_engine  # placeholder to appease linters; unused below
    del app_session_maker

    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    app_session_maker = async_sessionmaker(app_engine, expire_on_commit=False, class_=AsyncSession)

    async def _read_back_setting() -> str | None:
        async with app_session_maker() as session:
            result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            return result.scalar()

    async def _set_and_use(tenant_id: uuid.UUID) -> None:
        async with app_session_maker() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            assert result.scalar() == str(tenant_id)
            await session.commit()

    for _ in range(3):
        leaked = await _read_back_setting()
        assert leaked in (None, ""), f"app.tenant_id leaked into a fresh app-role session: {leaked!r}"
        await _set_and_use(tenant_a)
        leaked = await _read_back_setting()
        assert leaked in (None, ""), f"tenant A's context leaked past its own transaction (app role): {leaked!r}"
        await _set_and_use(tenant_b)
        leaked = await _read_back_setting()
        assert leaked in (None, ""), f"tenant B's context leaked past its own transaction (app role): {leaked!r}"


@requires_real_postgres
async def test_provisioning_is_idempotent(app_role) -> None:
    """Running the provisioning script a second time against an
    already-provisioned role must not error and must leave the role's
    restricted attributes unchanged — this is the exact operation a
    redeploy or a second `docker compose up` performs."""
    app_user, app_engine = app_role
    await provision_app_role(
        owner_dsn=str(owner_engine.url).replace("postgresql+asyncpg", "postgresql"),
        app_user=app_user,
        app_password=uuid.uuid4().hex,
    )
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :name"),
                {"name": app_user},
            )
        ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False
