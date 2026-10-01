"""Phase 17B-4 tier 2 (Round 5): real, enforcing tenant-isolation RLS
policies on a second batch of 10 tables — continuing the rollout begun by
`0053` (see PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md §17b/§34/§35 for the
proven pattern and the running per-table tally).

This batch was chosen deliberately, not arbitrarily, to make direct
progress on two of the coordinator's open items at once:

- The agent-runtime family (`agents`, `agent_versions`,
  `agent_tool_permissions`, `agent_executions`, `agent_execution_steps`)
  — 5 tables that already carried the Phase-0 audit-mode permissive
  policy (`tenant_isolation_audit_policy`, added by migration `0045`/
  `0046`) and are converted IN PLACE to real enforcement here, exactly
  like `0053` converted `users`. `agents`/`agent_versions`/
  `agent_executions` are also 3 of the 5 tables this same round's
  `backend/scripts/db/provision_discovery_role.py` update extends
  `klaros_discovery`'s column-level grants to (Agent discovery, Agent
  Recovery discovery) — having real RLS land in the same round as the
  discovery grants keeps the discovery-role carve-out policy
  (`discovery_select`, created by that script, see its Round 5 docstring
  update) meaningful immediately rather than provisioned against a table
  with no RLS to carve out from yet.
- The automation-execution family (`automation_versions`,
  `automation_executions`, `automation_execution_steps`) — 3 tables that
  had NO RLS at all before this migration. `automation_versions` is the
  other table the Automation-discovery grant needs (`automations` itself
  already got real RLS in `0053`); leaving it out this round would have
  left the discovery grant partially meaningless.
- Two ordinary financial tables with no RLS at all before this migration
  (`invoice_line_items`, `payments`) — continuing the plain-
  TENANT_SCOPED-table rollout the coordinator asked to keep moving in
  reviewed batches, not just the discovery-adjacent tables.

10 tables total. All were confirmed via the live catalog audit (§4-9 /
this round's re-check) to have a `tenant_id` column that is indexed and
`NOT NULL` (none of them is the `webhook_events` nullable-tenant_id
special case `0053` already handled) — no data-model semantics are
changed by this migration, only RLS policy objects are added/replaced.

Four separate per-command policies per table (SELECT/INSERT/UPDATE/
DELETE), not one `FOR ALL`, matching the proven `0053` pattern exactly:
  USING (tenant_id = current_tenant_id())              -- SELECT/UPDATE/DELETE
  WITH CHECK (tenant_id = current_tenant_id())          -- INSERT/UPDATE

Requires `0052`'s `current_tenant_id()` function. PostgreSQL-only, no-op
on SQLite, matching every RLS migration since `0040`.

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_POLICY_NAME = "tenant_isolation_audit_policy"

# Tables that already had RLS enabled with the Phase-0 audit-mode
# permissive policy (0045/0046) — this migration only swaps the policy,
# it does not touch ENABLE ROW LEVEL SECURITY, which is already on.
_ALREADY_AUDIT_MODE_TABLES = (
    "agents",
    "agent_versions",
    "agent_tool_permissions",
    "agent_executions",
    "agent_execution_steps",
)

# Tables with no RLS at all before this migration — need ENABLE ROW LEVEL
# SECURITY plus the 4 real policies, no audit-mode policy to drop first.
_NEWLY_ENFORCED_TABLES = (
    "automation_versions",
    "automation_executions",
    "automation_execution_steps",
    "invoice_line_items",
    "payments",
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
