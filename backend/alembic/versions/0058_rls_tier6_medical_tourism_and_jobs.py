"""Phase 17B-4 tier 6 (Round 9): real, enforcing tenant-isolation RLS
policies on a sixth batch of 13 tables — continuing the rollout begun by
`0053`-`0057` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b/§17c/§17d/§17e/§17f/§34/§35 for the proven pattern and the running
per-table tally).

This batch, like the previous five, was chosen deliberately:

- **All 7 Medical Tourism tables** (`0049`), converted in place from
  audit-mode to real enforcement: `medical_tourism_providers`,
  `medical_tourism_provider_credentials`, `medical_tourism_procedures`,
  `medical_tourism_provider_procedures`, `medical_tourism_patient_leads`,
  `medical_tourism_consultations`, `medical_tourism_referral_commissions`.
  **This closes out every already-audit-mode table this phase started
  with — zero tables remain in Phase-0 permissive audit-mode after this
  migration.** Medical Tourism is the vertical this security track has
  been explicitly building toward per the original brief (the first real,
  shipped vertical extension on the platform), so it is given the exact
  same full rigor as every other batch — migration-cycle proof plus a
  spot-checked sample of real-Postgres isolation checks (§17g below) —
  not fast-tracked or treated as a formality just because it happens to
  be a single coherent domain landing all at once.
- Six ordinary TENANT_SCOPED tables with no RLS at all before this
  migration, from the Jobs and Finance domains — a natural pairing with
  `jobs`/`invoices`/`payments`/`refunds`'s siblings already converted in
  earlier rounds: `job_costs`, `job_materials`, `job_tasks`, `job_qa`,
  `job_attachments`, `refunds`.

13 tables total: 7 already-audit-mode conversions + 6 newly-enforced
plain tables.

All 13 were confirmed via a live query against a freshly
`alembic upgrade head`-ed database (this round) to have a `tenant_id`
column that is indexed and `NOT NULL` — none is a second
`webhook_events`-style nullable-tenant_id special case. No data-model
semantics are changed by this migration, only RLS policy objects are
added/replaced.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053`-`0057` pattern
exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0058
Revises: 0057
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_POLICY_NAME = "tenant_isolation_audit_policy"

# Tables that already had RLS enabled with the Phase-0 audit-mode
# permissive policy (0049) — this migration only swaps the policy, it
# does not touch ENABLE ROW LEVEL SECURITY, which is already on. This is
# the LAST audit-mode cohort — after this migration, zero tables remain
# in permissive audit-mode anywhere in the schema.
_ALREADY_AUDIT_MODE_TABLES = (
    "medical_tourism_providers",
    "medical_tourism_provider_credentials",
    "medical_tourism_procedures",
    "medical_tourism_provider_procedures",
    "medical_tourism_patient_leads",
    "medical_tourism_consultations",
    "medical_tourism_referral_commissions",
)

# Tables with no RLS at all before this migration — need ENABLE ROW LEVEL
# SECURITY plus the 4 real policies, no audit-mode policy to drop first.
_NEWLY_ENFORCED_TABLES = (
    "job_costs",
    "job_materials",
    "job_tasks",
    "job_qa",
    "job_attachments",
    "refunds",
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
