"""Phase 17B-4 tier 9 (Round 12): real, enforcing tenant-isolation RLS
policies on a ninth batch of 15 tables — continuing the rollout begun by
`0053`-`0060` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b/§17c/§17d/§17e/§17f/§17g/§17h/§17i/§34/§35 for the proven pattern and
the running per-table tally).

All 15 tables are newly RLS-enabled — no audit-mode conversions remain
anywhere in the schema (retired in Round 9's `0058`). This batch spans
five adjacent marketing/operations domains, deliberately not clustered
into one:

- Nurture (3): `nurture_sequences`, `nurture_enrollments`,
  `nurture_activities`.
- Reactivation (2): `reactivation_campaigns`, `reactivation_candidates`.
- SEO (3): `seo_pages`, `seo_keywords`, `seo_opportunities`.
- Local/Reputation (3): `local_listings`, `local_reviews`,
  `local_reputation_events`.
- Vendor/Purchasing (4): `vendors`, `vendor_bills`, `purchase_orders`,
  `purchase_order_items`.

15 tables total, all confirmed via a live query against a freshly
`alembic upgrade head`-ed database (this round) to have a `tenant_id`
column that is indexed and `NOT NULL` — none is a second
`webhook_events`-style nullable-tenant_id special case. No data-model
semantics are changed by this migration, only RLS policy objects are
added.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`-`0060` pattern
exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0061
Revises: 0060
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All 15 tables are newly enforced — no audit-mode cohort remains
# anywhere in the schema (retired in Round 9's 0058).
_NEWLY_ENFORCED_TABLES = (
    "nurture_sequences",
    "nurture_enrollments",
    "nurture_activities",
    "reactivation_campaigns",
    "reactivation_candidates",
    "seo_pages",
    "seo_keywords",
    "seo_opportunities",
    "local_listings",
    "local_reviews",
    "local_reputation_events",
    "vendors",
    "vendor_bills",
    "purchase_orders",
    "purchase_order_items",
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
