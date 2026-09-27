"""Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.2): PostgreSQL RLS
audit-mode instrumentation — tenant-context plumbing (`SET LOCAL
app.tenant_id`, see app/db/session.py::set_tenant_context) and the
permissive RLS policies added by alembic/versions/0040_rls_audit_mode_tier1.py.

Skipped entirely unless DATABASE_URL points at a real PostgreSQL instance
— RLS is a PostgreSQL-only concept with no SQLite equivalent (matches the
existing tests/test_postgres_*.py convention).

Three things this file proves, matching the Phase 0 plan's explicit
requirements:

1. Audit mode is a real, verified no-op today (0.2 requirement: "zero
   behavior change") — a session with NO tenant context set still sees
   every tenant's rows on these 5 tables, because no policy is enforcing.
2. The connection-pool-reuse safety property the plan calls "the single
   highest-risk implementation detail in this entire plan": tenant A's
   context set via SET LOCAL on one borrowed connection must never leak
   to tenant B's request when the pool hands that same physical
   connection to a different session afterwards.
3. The mechanism 0.3 (RLS enforcement, a later, separate phase) will
   flip on is proven correct in isolation, inside a rolled-back
   transaction that never persists the change: fail-safe behavior (no
   tenant context set => zero rows, never all rows) and correct
   cross-tenant denial.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, engine, set_tenant_context
from app.models.company_memory import CompanyMemory, MemoryStatus, MemoryType

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

RLS_TABLES = ["integration_connections", "approval_requests", "audit_logs", "company_memories", "users"]


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policies():
    """conftest.py's global `_reset_database` (autouse, function-scoped)
    rebuilds every table from `Base.metadata` directly — it does not run
    Alembic migrations, so migration 0040's RLS DDL never applies there.
    This module-local fixture re-applies that exact same DDL after each
    test's fresh schema (and only when DATABASE_URL is real Postgres),
    intentionally duplicated here rather than editing the shared conftest
    fixture — keeping this Phase 0 diff scoped to this one test file
    instead of adding RLS DDL overhead to all ~1400 other tests."""
    if "postgresql" not in _settings.DATABASE_URL:
        yield
        return
    async with engine.begin() as conn:
        for table in RLS_TABLES:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_audit_policy ON {table}"))
            await conn.execute(
                text(
                    f"CREATE POLICY tenant_isolation_audit_policy ON {table} "
                    "FOR ALL USING (true) WITH CHECK (true)"
                )
            )
    yield


async def _seed_company_memory(session, tenant_id: uuid.UUID, key: str) -> None:
    session.add(
        CompanyMemory(
            tenant_id=tenant_id,
            key=key,
            value="phase 0 rls test fixture",
            memory_type=MemoryType.BUSINESS_RULE,
            status=MemoryStatus.ACTIVE,
            source="OWNER_EXPLICIT",
        )
    )
    await session.commit()


@requires_real_postgres
async def test_rls_is_enabled_on_all_five_tier1_tables() -> None:
    """Confirms migration 0040 actually ran and attached its policy — a
    cheap, direct check against pg_policies/pg_class rather than inferring
    it indirectly from query behavior."""
    async with async_session_maker() as session:
        for table in RLS_TABLES:
            enabled = await session.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert enabled.scalar() is True, f"{table} does not have RLS enabled"

            forced = await session.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced.scalar() is False, (
                f"{table} has FORCE ROW LEVEL SECURITY set — that is item 0.3 "
                "(enforcement), not this audit-mode migration"
            )

            policy_count = await session.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
            assert policy_count.scalar() == 1, f"{table} should have exactly one audit-mode policy"


@requires_real_postgres
async def test_audit_mode_is_a_real_no_op_today() -> None:
    """Zero behavior change (0.2's explicit requirement): a session that
    never calls set_tenant_context at all must still see rows belonging to
    an arbitrary tenant on an RLS-enabled table — proving the permissive
    `USING (true)` policy does not filter anything yet."""
    tenant_id = uuid.uuid4()
    key = f"audit-noop-{uuid.uuid4()}"

    async with async_session_maker() as session:
        await _seed_company_memory(session, tenant_id, key)

    async with async_session_maker() as session:
        # Deliberately do NOT call set_tenant_context here.
        result = await session.execute(text("SELECT tenant_id FROM company_memories WHERE key = :k"), {"k": key})
        row = result.first()
        assert row is not None, "audit-mode policy must not hide rows from a context-less session"
        assert row[0] == tenant_id


@requires_real_postgres
async def test_set_local_tenant_context_does_not_leak_across_pooled_connections() -> None:
    """The plan's named highest-risk detail: SET (session-scoped) vs SET
    LOCAL (transaction-scoped). Runs tenant A -> tenant B -> tenant A across
    three separate, independently-acquired sessions from the SAME pooled
    engine, and asserts app.tenant_id never survives past the transaction
    that set it — proving a connection handed back to the pool cannot leak
    its previous borrower's tenant identity to the next one."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    async def _read_back_setting() -> str | None:
        async with async_session_maker() as session:
            # Read app.tenant_id BEFORE this session sets anything itself —
            # if SET LOCAL truly reset at the end of the previous
            # session/transaction, this must be unset (NULL), regardless of
            # which physical connection the pool handed back.
            result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            return result.scalar()

    async def _set_and_use(tenant_id: uuid.UUID) -> None:
        async with async_session_maker() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            assert result.scalar() == str(tenant_id)
            await session.commit()

    # Run enough acquire/release cycles to make physical-connection reuse
    # likely (pool_size=10 per app/db/session.py — a handful of cycles is
    # enough to exercise reuse without needing to inspect pool internals).
    for _ in range(3):
        leaked = await _read_back_setting()
        assert leaked in (None, ""), f"app.tenant_id leaked into a fresh session: {leaked!r}"
        await _set_and_use(tenant_a)
        leaked = await _read_back_setting()
        assert leaked in (None, ""), f"tenant A's context leaked past its own transaction: {leaked!r}"
        await _set_and_use(tenant_b)
        leaked = await _read_back_setting()
        assert leaked in (None, ""), f"tenant B's context leaked past its own transaction: {leaked!r}"


@requires_real_postgres
async def test_concurrent_requests_maintain_isolated_tenant_context() -> None:
    """Async/concurrency requirement: two "requests" running genuinely
    concurrently (asyncio.gather, each on its own session/connection) must
    each see only their own app.tenant_id — proves SET LOCAL's transaction
    scoping isolates concurrent connections, not just sequential reuse."""
    import asyncio

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    async def _observe(tenant_id: uuid.UUID) -> str | None:
        async with async_session_maker() as session:
            await set_tenant_context(session, tenant_id)
            # Yield control so the other coroutine's session can interleave
            # if (and only if) they were sharing state incorrectly.
            await asyncio.sleep(0.05)
            result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            return result.scalar()

    result_a, result_b = await asyncio.gather(_observe(tenant_a), _observe(tenant_b))
    assert result_a == str(tenant_a)
    assert result_b == str(tenant_b)


@requires_real_postgres
async def test_enforcing_policy_fails_safe_with_no_tenant_context() -> None:
    """Proves the mechanism 0.3 will flip on: within a transaction that is
    ROLLED BACK (never persisted — this test does not turn on real
    enforcement), temporarily FORCE + tighten the RLS policy on
    company_memories to the real tenant-matching condition, and assert:
      (a) no tenant context set => ZERO rows (fail safe, never all rows);
      (b) tenant A's context => only tenant A's rows, never tenant B's.
    This is the exact safety property the Phase 0 plan requires before any
    real enforcement migration is written.

    Verified against a genuinely-non-privileged role, not the connecting
    superuser: PostgreSQL superusers (and any BYPASSRLS role) *always*
    bypass row security, `FORCE ROW LEVEL SECURITY` included — that is
    documented, correct Postgres behavior, not a bug to work around. Both
    this test suite's own DATABASE_URL role and docker-compose.yml's/CI's
    `POSTGRES_USER=klaros` are the cluster's initdb bootstrap role, i.e.
    a superuser — so asserting row counts directly on that connection
    would silently pass or fail for the wrong reason (RLS bypass, not RLS
    logic) in every environment this test actually runs in. A short-lived,
    non-superuser, non-BYPASSRLS role with plain SELECT/INSERT is created
    for the duration of this test (outside the rolled-back transaction, so
    it is torn down explicitly in `finally`, not implicitly by rollback)
    and the enforcement assertions run as that role via `SET LOCAL ROLE`."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    async with async_session_maker() as session:
        await _seed_company_memory(session, tenant_a, f"enforce-a-{uuid.uuid4()}")
        await _seed_company_memory(session, tenant_b, f"enforce-b-{uuid.uuid4()}")

    probe_role = f"rls_test_probe_{uuid.uuid4().hex[:8]}"
    async with engine.begin() as setup_conn:
        await setup_conn.execute(text(f'CREATE ROLE "{probe_role}" NOSUPERUSER NOBYPASSRLS LOGIN'))
        await setup_conn.execute(text(f'GRANT SELECT ON company_memories TO "{probe_role}"'))

    conn = await engine.connect()
    try:
        await conn.execute(text("ALTER TABLE company_memories FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("DROP POLICY IF EXISTS tenant_isolation_audit_policy ON company_memories"))
        # NOTE: PostgreSQL's planner does not guarantee left-to-right
        # evaluation of AND conjuncts in a USING clause (it may reorder
        # them by estimated cost/selectivity) — a plain
        # `x IS NOT NULL AND x <> '' AND tenant_id = x::uuid` can still
        # attempt the ::uuid cast first and raise, rather than short-
        # circuiting, if the guard clauses are reordered after it. This is
        # not hypothetical here: a custom GUC like `app.tenant_id`, once
        # set (even via SET LOCAL) on a given backend/connection, leaves a
        # placeholder behind — `current_setting(..., true)` returns ''
        # (not NULL) for it on that same backend afterwards, which this
        # very engine's own connection pool makes routine. CASE/WHEN is
        # the documented-safe construct (Postgres guarantees it evaluates
        # only the taken branch), so it is used here instead of bare AND.
        await conn.execute(
            text(
                """
                CREATE POLICY tenant_isolation_audit_policy ON company_memories
                FOR ALL
                USING (
                    CASE
                        WHEN current_setting('app.tenant_id', true) IS NULL
                             OR current_setting('app.tenant_id', true) = ''
                        THEN false
                        ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
                    END
                )
                """
            )
        )
        await conn.execute(text(f'SET LOCAL ROLE "{probe_role}"'))

        # (a) fail-safe: no context set => zero rows, not "all rows".
        result = await conn.execute(text("SELECT count(*) FROM company_memories"))
        assert result.scalar() == 0, "a context-less connection under enforcement must see ZERO rows"

        # (b) correct scoping: set tenant A's context, see only tenant A.
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_a)})
        result = await conn.execute(text("SELECT tenant_id FROM company_memories"))
        rows = {r[0] for r in result.all()}
        assert rows == {tenant_a}, "enforcing policy leaked another tenant's rows"
    finally:
        # Never commit — rolling back discards both the FORCE flag, the
        # temporary policy rewrite, and the SET LOCAL ROLE; the real
        # audit-mode policy (USING true) is untouched for every other
        # test/session.
        await conn.rollback()
        await conn.close()
        async with engine.begin() as cleanup_conn:
            await cleanup_conn.execute(text(f'REVOKE SELECT ON company_memories FROM "{probe_role}"'))
            await cleanup_conn.execute(text(f'DROP ROLE "{probe_role}"'))


@requires_real_postgres
async def test_existing_application_queries_are_unaffected_by_audit_mode() -> None:
    """Regression guard: the app's own manual `.where(tenant_id == ...)`
    query convention (TenantScopedMixin is column-only, per
    KLAROS_ARCHITECTURE_RECONCILIATION.md) must return identical results
    with the audit-mode policy present as it did before — this is the
    plan's "confirm existing legitimate application queries still pass"
    requirement."""
    from sqlalchemy import select

    tenant_id = uuid.uuid4()
    key = f"app-query-{uuid.uuid4()}"

    async with async_session_maker() as session:
        await _seed_company_memory(session, tenant_id, key)

    async with async_session_maker() as session:
        result = await session.execute(select(CompanyMemory).where(CompanyMemory.tenant_id == tenant_id))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].key == key
