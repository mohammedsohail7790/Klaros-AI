"""Phase 17B-4 tier 1: real, enforcing tenant-isolation RLS policies (not
audit-mode) on the 5 tables the policy pattern was proven against in
PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md §17b — `customers`,
`webhook_events`, `automations`, `users`, `invoices` — chosen deliberately
to span every relevant classification found in the phase's live-Postgres
catalog audit (§4-9): an ordinary simple tenant table, the one table with
a nullable `tenant_id`, a table also read cross-tenant by a system
discovery sweep, a table upgraded IN PLACE from an existing Phase-0
audit-mode permissive policy to real enforcement, and a financial/
FK-heavy table.

Deliberately NOT the other ~95 tenant-owned tables, and NOT
`FORCE ROW LEVEL SECURITY` anywhere — both explicitly out of scope for
this migration; see the log's running tally for exactly which tables have
real policies vs. audit-mode vs. untouched after this migration.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven pattern exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

`users`: this table already carried a Phase-0 (migration 0040) permissive
audit-mode policy, `tenant_isolation_audit_policy`
(`USING(true) WITH CHECK(true)`, a deliberate no-op). This migration
DROPs that policy and replaces it with the 4 real ones — proven safe as
an in-place conversion in the log's §17b validation. The other 4 tables
here (`customers`, `webhook_events`, `automations`, `invoices`) had no
RLS at all before this migration (confirmed via the live catalog audit),
so they additionally need `ENABLE ROW LEVEL SECURITY` first.

`webhook_events.tenant_id` is nullable (the one such case in the schema —
a webhook can arrive before tenant resolution). Under these policies, a
row with `tenant_id IS NULL` is invisible under every tenant context
(`NULL = current_tenant_id()` is never true, even for a real, valid
tenant), by design — proven in §17b. Whatever currently creates/reads
those pre-resolution NULL-tenant rows must NOT be an ordinary
`klaros_app` tenant-context session; it needs its own system identity,
tracked as follow-up work (not this migration's job — this migration only
adds the isolation policies, it does not change what code path writes
NULL-tenant webhook rows today).

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since 0040.

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_POLICY_NAME = "tenant_isolation_audit_policy"

# Tables that already had RLS enabled with the Phase-0 audit-mode permissive
# policy (so this migration only swaps the policy, it does not touch
# ENABLE ROW LEVEL SECURITY, which is already on).
_ALREADY_AUDIT_MODE_TABLES = ("users",)

# Tables with no RLS at all before this migration — need ENABLE ROW LEVEL
# SECURITY plus the 4 real policies, no audit-mode policy to drop first.
_NEWLY_ENFORCED_TABLES = ("customers", "webhook_events", "automations", "invoices")

_ALL_TABLES = _ALREADY_AUDIT_MODE_TABLES + _NEWLY_ENFORCED_TABLES


def _create_real_policies(table: str) -> None:
    op.execute(
        f"""
        CREATE POLICY tenant_select ON {table} FOR SELECT
        USING (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY tenant_insert ON {table} FOR INSERT
        WITH CHECK (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY tenant_update ON {table} FOR UPDATE
        USING (tenant_id = current_tenant_id())
        WITH CHECK (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY tenant_delete ON {table} FOR DELETE
        USING (tenant_id = current_tenant_id())
        """
    )


def _drop_real_policies(table: str) -> None:
    for policy in ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in _ALREADY_AUDIT_MODE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {AUDIT_POLICY_NAME} ON {table}")
        _create_real_policies(table)

    for table in _NEWLY_ENFORCED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        _create_real_policies(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in _NEWLY_ENFORCED_TABLES:
        _drop_real_policies(table)
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in _ALREADY_AUDIT_MODE_TABLES:
        _drop_real_policies(table)
        op.execute(
            f"""
            CREATE POLICY {AUDIT_POLICY_NAME} ON {table}
            FOR ALL
            USING (true)
            WITH CHECK (true)
            """
        )
