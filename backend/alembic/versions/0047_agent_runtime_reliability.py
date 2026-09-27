"""Phase 6 (Agent Runtime Reliability — crash recovery, scheduled/event
triggers): additive execution-lease columns on the existing
`agent_executions` table. No new tables are created by this migration —
see PHASE_6_IMPLEMENTATION_LOG.md's Architecture Reconciliation for why:
trigger configuration reuses the existing, previously-unwired
`AgentVersion.triggers` JSON column (modeled since Phase 4 specifically
for "schedule cron expression and/or event-type subscriptions"), and
scheduled-occurrence/event-delivery idempotency reuses the existing
`AgentExecution.idempotency_key` unique constraint
(`uq_agent_executions_tenant_agent_idempotency`) with a deterministic key,
exactly mirroring how `AutomationExecution.source_event_id` +
`uq_automation_execution_version_event` already work for the Automation
Engine.

New columns on `agent_executions` (`execution_owner_id`,
`lease_expires_at`, `heartbeat_at`, `recovery_attempt_count`) implement the
durable, DB-backed execution lease `AgentRecoveryService` claims atomically
via a single conditional `UPDATE ... WHERE status='RUNNING' AND
(lease_expires_at IS NULL OR lease_expires_at < now())` — see
app/services/agent_recovery_service.py's module docstring for the full
design and app/models/agent.py's Phase 6 column comments for the exact
semantics of each column.

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_executions", sa.Column("execution_owner_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column(
        "agent_executions", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("agent_executions", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agent_executions",
        sa.Column("recovery_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_agent_executions_status_lease", "agent_executions", ["status", "lease_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_executions_status_lease", table_name="agent_executions")
    op.drop_column("agent_executions", "recovery_attempt_count")
    op.drop_column("agent_executions", "heartbeat_at")
    op.drop_column("agent_executions", "lease_expires_at")
    op.drop_column("agent_executions", "execution_owner_id")
