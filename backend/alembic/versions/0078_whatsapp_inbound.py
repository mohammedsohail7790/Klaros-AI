"""WhatsApp: inbound messages (also the 24-hour reply window record).

Additive only: one new table with row-level security on PostgreSQL (tenant_id = current_tenant_id()) like 0072-0077.

Rollback: `alembic downgrade 0077_bank_feed` drops the table (stored inbound messages and window records are lost).

Revision ID: 0078_whatsapp_inbound
Revises: 0077_bank_feed
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0078_whatsapp_inbound"
down_revision: Union[str, None] = "0077_bank_feed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "whatsapp_inbound_messages",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("wa_id", sa.String(32), nullable=False),
        sa.Column("phone_normalized", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=True),
        sa.Column("entity_id", _UUID, nullable=True),
        sa.Column("message_type", sa.String(30), nullable=False),
        sa.Column("profile_name", sa.String(255), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_stored", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "message_id", name="uq_whatsapp_inbound_message"),
    )
    op.create_index("ix_whatsapp_inbound_messages_tenant_id", "whatsapp_inbound_messages", ["tenant_id"])
    op.create_index("ix_whatsapp_inbound_messages_entity_id", "whatsapp_inbound_messages", ["entity_id"])
    op.create_index("ix_whatsapp_inbound_phone", "whatsapp_inbound_messages", ["tenant_id", "phone_normalized"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE whatsapp_inbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON whatsapp_inbound_messages FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON whatsapp_inbound_messages FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_update ON whatsapp_inbound_messages FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON whatsapp_inbound_messages")
    op.drop_table("whatsapp_inbound_messages")
