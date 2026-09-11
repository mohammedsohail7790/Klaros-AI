"""Phase 4 (AI Voice Receptionist): CallSession + VoiceReceptionistSettings.

See app/models/voice.py — CallSession never stores raw audio, only a
bounded text transcript; VoiceReceptionistSettings is one row per tenant.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="twilio"),
        sa.Column("external_call_id", sa.String(length=100), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False, server_default="INBOUND"),
        sa.Column("caller_number", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="RINGING"),
        sa.Column("outcome", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("appointment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("handoff_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("handoff_reason", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("transcript", sa.JSON(), nullable=False),
        sa.Column("engine_state", sa.JSON(), nullable=False),
        sa.UniqueConstraint("provider", "external_call_id", name="uq_call_sessions_provider_external_id"),
    )
    op.create_index("ix_call_sessions_tenant_id", "call_sessions", ["tenant_id"])
    op.create_index("ix_call_sessions_external_call_id", "call_sessions", ["external_call_id"])

    op.create_table(
        "voice_receptionist_settings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("greeting", sa.Text(), nullable=False),
        sa.Column("business_hours_note", sa.Text(), nullable=True),
        sa.Column("voice_name", sa.String(length=100), nullable=True),
        sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_voice_settings_tenant"),
    )
    op.create_index("ix_voice_receptionist_settings_tenant_id", "voice_receptionist_settings", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_receptionist_settings_tenant_id", table_name="voice_receptionist_settings")
    op.drop_table("voice_receptionist_settings")
    op.drop_index("ix_call_sessions_external_call_id", table_name="call_sessions")
    op.drop_index("ix_call_sessions_tenant_id", table_name="call_sessions")
    op.drop_table("call_sessions")
