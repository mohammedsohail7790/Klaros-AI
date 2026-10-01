"""Phase 17B-4 tier 4 (Round 7): real, enforcing tenant-isolation RLS
policies on a fourth batch of 12 tables — continuing the rollout begun by
`0053`/`0054`/`0055` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b/§17c/§17d/§34/§35 for the proven pattern and the running per-table
tally).

This batch, like the previous three, was chosen deliberately:

- The Business Discovery / Blueprint / Journey domain's already-audit-mode
  tables (`0043`/`0051`), converted in place: `discovery_sessions`,
  `discovery_turns`, `business_blueprints`, `blueprint_sections`,
  `blueprint_claims`, `business_journeys` — a coherent 6-table family (the
  full Discovery -> Blueprint -> Journey pipeline), matching the
  coordinator's "mix of remaining plain TENANT_SCOPED and remaining
  audit-mode conversions" instruction while keeping each batch's
  audit-mode half thematically grouped, same as Round 6's "tier 1
  completion" and Round 5's "agent-runtime family" batches.
- Six ordinary TENANT_SCOPED tables with no RLS at all before this
  migration, again spread across domains rather than clustering:
  `appointments`, `call_sessions`, `campaigns`, `campaign_leads`,
  `notification_preferences`, `workers`.

12 tables total: 6 already-audit-mode conversions + 6 newly-enforced
plain tables.

All 12 were confirmed via a live query against a freshly
`alembic upgrade head`-ed database (this round) to have a `tenant_id`
column that is indexed and `NOT NULL` — none is a second
`webhook_events`-style nullable-tenant_id special case. No data-model
semantics are changed by this migration, only RLS policy objects are
added/replaced.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`/`0054`/`0055`
pattern exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0056
Revises: 0055
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_POLICY_NAME = "tenant_isolation_audit_policy"

# Tables that already had RLS enabled with the Phase-0 audit-mode
# permissive policy (0043/0051) — this migration only swaps the policy,
# it does not touch ENABLE ROW LEVEL SECURITY, which is already on.
_ALREADY_AUDIT_MODE_TABLES = (
    "discovery_sessions",
    "discovery_turns",
    "business_blueprints",
    "blueprint_sections",
    "blueprint_claims",
    "business_journeys",
)

# Tables with no RLS at all before this migration — need ENABLE ROW LEVEL
# SECURITY plus the 4 real policies, no audit-mode policy to drop first.
_NEWLY_ENFORCED_TABLES = (
    "appointments",
    "call_sessions",
    "campaigns",
    "campaign_leads",
    "notification_preferences",
    "workers",
)

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
