"""Phase 17B-4 tier 10 (Round 13): real, enforcing tenant-isolation RLS
policies on a tenth batch of 13 tables — continuing the rollout begun by
`0053`-`0061` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b-§17j/§34/§35 for the proven pattern and the running per-table
tally).

All 13 tables are newly RLS-enabled — no audit-mode conversions remain
anywhere in the schema (retired in Round 9's `0058`). This batch spans
several domains:

- Retention/CRM (4): `advocate_candidates`, `customer_lifecycle_profiles`,
  `customer_risk_signals`, `collection_actions`.
- AI/Ops observability (3): `ai_invocation_logs`, `completion_packets`,
  `communication_logs`.
- Marketing (1): `campaign_conversions`.
- Event infrastructure (3): `events`, `event_processing_records`,
  `dead_letter_events`.
- Knowledge (2): `knowledge_files`, `knowledge_chunks`.

**Special note on `events`:** this table already carries a `discovery_select`
policy (`FOR SELECT TO klaros_discovery USING (true)`), created by
`backend/scripts/db/provision_discovery_role.py` back in Round 4 for the
EventBus stuck-event discovery path (§11b) — at that point `events` had
no RLS enabled at all, so the policy existed but was inert (a policy has
no effect until `ENABLE ROW LEVEL SECURITY` is set on its table). This
migration is the first time `events` gets real RLS enabled, which makes
that pre-existing `discovery_select` policy become LIVE for the first
time. This is intentional and by design (see §11c's original reasoning):
PostgreSQL ORs multiple applicable permissive policies together, so:
  - `klaros_app` (an ordinary tenant session) is governed only by the 4
    new `tenant_*` policies added here — `discovery_select` does not name
    `klaros_app`, so it has zero effect on that role's queries.
  - `klaros_discovery` is governed by `tenant_select OR discovery_select`
    — `tenant_select` always evaluates false for it (its session never
    calls `set_tenant_context`, so `current_tenant_id()` is NULL), but
    `discovery_select` evaluates true for every row, giving it the exact
    same narrow (id/tenant_id/event_type/status/created_at column-level
    grant) cross-tenant read it already had — now for real, since RLS is
    finally on.
This migration does NOT touch `discovery_select` at all (not dropped, not
recreated) — it is untouched, pre-existing, and orthogonal to the 4 new
policies added here. §17k documents the real-Postgres proof that both
mechanisms coexist correctly after this migration.

All 13 tables confirmed via a live query against a freshly `alembic
upgrade head`-ed database (this round) to have a `tenant_id` column that
is indexed and `NOT NULL` — none is a second `webhook_events`-style
nullable-tenant_id special case. No data-model semantics are changed by
this migration, only RLS policy objects are added.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`-`0061` pattern
exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0062
Revises: 0061
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All 13 tables are newly enforced — no audit-mode cohort remains
# anywhere in the schema (retired in Round 9's 0058).
_NEWLY_ENFORCED_TABLES = (
    "advocate_candidates",
    "customer_lifecycle_profiles",
    "customer_risk_signals",
    "collection_actions",
    "ai_invocation_logs",
    "completion_packets",
    "communication_logs",
    "campaign_conversions",
    "events",
    "event_processing_records",
    "dead_letter_events",
    "knowledge_files",
    "knowledge_chunks",
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
