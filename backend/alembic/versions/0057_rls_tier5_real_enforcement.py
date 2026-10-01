"""Phase 17B-4 tier 5 (Round 8): real, enforcing tenant-isolation RLS
policies on a fifth batch of 13 tables — continuing the rollout begun by
`0053`-`0056` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b/§17c/§17d/§17e/§34/§35 for the proven pattern and the running
per-table tally).

This batch, like the previous four, was chosen deliberately:

- The platform-primitives already-audit-mode tables (`0048`/`0050`/`0041`),
  converted in place: `mcp_tool_exposures`, `mcp_client_credentials`
  (MCP server), `websites`, `website_versions`, `website_pages`,
  `website_sections` (Website Builder), `organization_vertical_extensions`
  (vertical extension registry join table). This completes every
  already-audit-mode table EXCEPT the 7 Medical Tourism tables (`0049`),
  which are deliberately left as a single coherent domain family for the
  next round rather than split across two migrations.
- Six ordinary TENANT_SCOPED tables with no RLS at all before this
  migration, all from the finance domain (a natural pairing with
  `invoices`/`payments`/`invoice_line_items` already converted in `0053`/
  `0054`, and with the CashForecast fix from an earlier round):
  `cash_forecasts`, `cash_forecast_items`, `payouts`, `payment_allocations`,
  `credit_notes`, `credit_note_line_items`.

13 tables total: 7 already-audit-mode conversions + 6 newly-enforced
plain tables.

All 13 were confirmed via a live query against a freshly
`alembic upgrade head`-ed database (this round) to have a `tenant_id`
column that is indexed and `NOT NULL` — none is a second
`webhook_events`-style nullable-tenant_id special case. No data-model
semantics are changed by this migration, only RLS policy objects are
added/replaced.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`-`0056` pattern
exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0057
Revises: 0056
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_POLICY_NAME = "tenant_isolation_audit_policy"

# Tables that already had RLS enabled with the Phase-0 audit-mode
# permissive policy (0041/0048/0050) — this migration only swaps the
# policy, it does not touch ENABLE ROW LEVEL SECURITY, which is already on.
_ALREADY_AUDIT_MODE_TABLES = (
    "mcp_tool_exposures",
    "mcp_client_credentials",
    "organization_vertical_extensions",
    "websites",
    "website_versions",
    "website_pages",
    "website_sections",
)

# Tables with no RLS at all before this migration — need ENABLE ROW LEVEL
# SECURITY plus the 4 real policies, no audit-mode policy to drop first.
_NEWLY_ENFORCED_TABLES = (
    "cash_forecasts",
    "cash_forecast_items",
    "payouts",
    "payment_allocations",
    "credit_notes",
    "credit_note_line_items",
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
