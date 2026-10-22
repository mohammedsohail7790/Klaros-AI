"""Clinic outreach shadow messages: one drafted email per clinic, kind and UTC day, plus campaigns.engine_last_run_at.

Additive only. clinic_outreach_messages has row-level security on PostgreSQL (tenant_id = current_tenant_id()) like 0080. Nothing here sends email.

Rollback: `alembic downgrade 0083_dropshipping_supplier_sync` drops the table (drafted shadow messages and review marks are lost) and the engine_last_run_at column.

Revision ID: 0084_clinic_outreach_messages
Revises: 0083_dropshipping_supplier_sync
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0084_clinic_outreach_messages"
down_revision: Union[str, None] = "0083_dropshipping_supplier_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLE = "clinic_outreach_messages"


def upgrade() -> None:
    with op.batch_alter_table("clinic_outreach_campaigns") as batch:
        batch.add_column(sa.Column("engine_last_run_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        _TABLE,
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("outreach_id", _UUID, sa.ForeignKey("clinic_outreach.id"), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("blocks_version", sa.Integer(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_day", sa.Date(), nullable=False),
        sa.Column("review_mark", sa.String(10), nullable=True),
        sa.Column("review_reason", sa.String(500), nullable=True),
        sa.Column("reviewed_by", _UUID, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("outreach_id", "kind", "schedule_day", name="uq_outreach_message_kind_day"),
    )
    op.create_index("ix_clinic_outreach_messages_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_clinic_outreach_messages_outreach_id", _TABLE, ["outreach_id"])
    op.create_index("ix_outreach_messages_tenant_status_scheduled", _TABLE, ["tenant_id", "status", "scheduled_for"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_select ON {_TABLE} FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_insert ON {_TABLE} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_update ON {_TABLE} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {_TABLE}")
    op.drop_table(_TABLE)
    with op.batch_alter_table("clinic_outreach_campaigns") as batch:
        batch.drop_column("engine_last_run_at")
