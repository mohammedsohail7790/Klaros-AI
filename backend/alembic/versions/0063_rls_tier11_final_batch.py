"""Phase 17B-4 tier 11 (Round 13, final batch): real, enforcing
tenant-isolation RLS policies on the eleventh and FINAL batch of 13
tables — completing the rollout begun by `0053`-`0062` (see
PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md §17b-§17k/§34/§35 for the
proven pattern and the running per-table tally).

This migration brings every one of the 132 TENANT_SCOPED tables found in
the phase's live-Postgres catalog audit (§4-9) to real, enforcing RLS.
**After this migration, table-level RLS coverage is 132/132 — complete.**
This does NOT by itself mean Phase 17B-4 is complete: per the
coordinator's explicit instruction, the full test matrix (Medical
Tourism/Website/MCP/Webhook/Agent E2E validation, the full backend
regression suite, a frontend confirmation pass, a secret scan, and a
static search for any RLS-bypass mechanism) is required before any
COMPLETE claim — see the log's §34/§36 for that gate.

All 13 tables are newly RLS-enabled — no audit-mode conversions remain
anywhere in the schema (retired in Round 9's `0058`). This batch spans
several remaining domains: `lead_attributions`, `licenses`,
`marketing_spend_allocations`, `operations_exceptions`,
`outbound_enrollments`, `review_requests`, `scope_changes`,
`service_reminders`, `team_invites`, `tenant_tool_policies`,
`voice_receptionist_settings`, `warranties`, `writeoff_requests`.

All 13 confirmed via a live query against a freshly `alembic upgrade
head`-ed database (this round) to have a `tenant_id` column that is
indexed and `NOT NULL` — none is a second `webhook_events`-style
nullable-tenant_id special case. No data-model semantics are changed by
this migration, only RLS policy objects are added.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`-`0062` pattern
exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All 13 tables are newly enforced — the final cohort. No audit-mode
# cohort remains anywhere in the schema (retired in Round 9's 0058).
_NEWLY_ENFORCED_TABLES = (
    "lead_attributions",
    "licenses",
    "marketing_spend_allocations",
    "operations_exceptions",
    "outbound_enrollments",
    "review_requests",
    "scope_changes",
    "service_reminders",
    "team_invites",
    "tenant_tool_policies",
    "voice_receptionist_settings",
    "warranties",
    "writeoff_requests",
)


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
