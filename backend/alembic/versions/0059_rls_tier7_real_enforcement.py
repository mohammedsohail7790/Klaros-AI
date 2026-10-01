"""Phase 17B-4 tier 7 (Round 10): real, enforcing tenant-isolation RLS
policies on a seventh batch of 13 tables — continuing the rollout begun
by `0053`-`0058` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b/§17c/§17d/§17e/§17f/§17g/§34/§35 for the proven pattern and the
running per-table tally).

Simpler than every prior batch in one respect: Round 9's `0058` converted
the last remaining Phase-0 audit-mode tables (the 7 Medical Tourism
tables), so there is no more "drop the existing audit-mode policy first"
step for any table anywhere in the schema — every table from here on is
a plain `ENABLE ROW LEVEL SECURITY` + 4-policy addition, the same shape
as this migration's `_NEWLY_ENFORCED_TABLES`-only half in every prior
batch.

This batch spans three coherent domains, deliberately not clustered into
one, continuing the "reviewed batch, mixed domains" approach used since
Round 6:

- Content/Marketing (7): `content_assets`, `content_performance`,
  `content_publications`, `content_variants`, `marketing_content`,
  `marketing_lead_sources`, `marketing_spend`.
- Morning Brief (3): `morning_briefs`, `morning_brief_insights`,
  `morning_brief_recommendations` — a natural pairing with `organizations`'
  morning-brief-scheduling columns already granted to `klaros_discovery`
  in Round 5 for the Morning Brief discovery path (§11c), though these
  3 tables themselves are read only inside a genuine per-tenant session
  by `MorningBriefService`, never by the discovery sweep itself.
- Customer (3): `customer_feedback`, `customer_notes`,
  `customer_signoffs`.

13 tables total, all newly RLS-enabled (no audit-mode conversions
remain anywhere).

All 13 were confirmed via a live query against a freshly
`alembic upgrade head`-ed database (this round) to have a `tenant_id`
column that is indexed and `NOT NULL` — none is a second
`webhook_events`-style nullable-tenant_id special case. No data-model
semantics are changed by this migration, only RLS policy objects are
added.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`-`0058` pattern
exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0059
Revises: 0058
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All 13 tables are newly enforced — no audit-mode cohort remains
# anywhere in the schema after 0058 (Round 9).
_NEWLY_ENFORCED_TABLES = (
    "content_assets",
    "content_performance",
    "content_publications",
    "content_variants",
    "marketing_content",
    "marketing_lead_sources",
    "marketing_spend",
    "morning_briefs",
    "morning_brief_insights",
    "morning_brief_recommendations",
    "customer_feedback",
    "customer_notes",
    "customer_signoffs",
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
