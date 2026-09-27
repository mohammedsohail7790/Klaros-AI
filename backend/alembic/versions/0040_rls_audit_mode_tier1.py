"""RLS audit-mode instrumentation on the 5 highest-sensitivity tenant tables

Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.2 — "RLS instrumentation
(permissive/audit mode, zero behavior change)"). See
PHASE_0_IMPLEMENTATION_LOG.md's RLS section for the full design writeup,
the tenant-context trace, and the pool-reuse safety argument.

Scope, exactly as approved (do not extend without a separate decision):
  - integration_connections
  - approval_requests
  - audit_logs
  - company_memories
  - users

(`organizations` itself is excluded: it IS the tenant root row, not a
tenant-scoped child table — its own `id` is the tenant identity, there is
no `tenant_id` column to filter on. The plan's "Organization/User" slash
reads as "pick whichever of the two is the real 5th TenantScopedMixin
table" — `users` is the one with an actual `tenant_id` column.)

AUDIT MODE, NOT ENFORCEMENT: each table gets `ENABLE ROW LEVEL SECURITY`
(not `FORCE`, so the migration/admin role and any connection that never
calls set_tenant_context still see all rows — no behavior change) plus one
permissive policy whose USING/WITH CHECK clause is literally `true`. This
is intentionally a no-op today. What it buys: (1) the policy objects exist
and are visibly attached to these 5 tables (`pg_policies`), so 0.3 is a
one-line `ALTER POLICY ... USING (tenant_id = current_setting(...))`
instead of a from-scratch schema change under time pressure; (2) proves
these 5 tables tolerate having RLS enabled at all (some ORM/driver
combinations behave surprisingly around RLS-enabled tables even with an
always-true policy) before anything real depends on it.

Requires PostgreSQL. No-op on SQLite (this project's test/dev fallback
engine) since SQLite has no RLS concept — see app/db/session.py's
set_tenant_context, which is equally a no-op there.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "integration_connections",
    "approval_requests",
    "audit_logs",
    "company_memories",
    "users",
]

POLICY_NAME = "tenant_isolation_audit_policy"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # RLS is a PostgreSQL-only concept. SQLite (dev/test fallback,
        # see app/db/session.py) has no equivalent and no `CREATE POLICY`
        # syntax — silently skipping here (rather than failing) matches
        # how the rest of this codebase already treats SQLite as a real,
        # supported dev/test target, not a second production engine.
        return

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # Deliberately NOT `FORCE ROW LEVEL SECURITY` — FORCE is what makes
        # RLS apply even to the table owner; leaving it off means Alembic's
        # own migration role and any as-yet-unmigrated code path keep
        # working exactly as before. That flip is explicitly item 0.3
        # (RLS enforcement), a later, separate Phase 0 step gated on a
        # clean week of this audit-mode data in staging.
        op.execute(
            f"""
            CREATE POLICY {POLICY_NAME} ON {table}
            FOR ALL
            USING (true)
            WITH CHECK (true)
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
