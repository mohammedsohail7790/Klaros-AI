"""Clinic outreach sending: when a message was sent and how many times a send was tried.

Additive and nullable or defaulted, so it is safe on a table already holding shadow drafts. Nothing here sends email by itself.

Rollback: `alembic downgrade 0084_clinic_outreach_messages` drops the two columns (send history is lost).

Revision ID: 0085_clinic_outreach_sending
Revises: 0084_clinic_outreach_messages
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0085_clinic_outreach_sending"
down_revision: Union[str, None] = "0084_clinic_outreach_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clinic_outreach_messages") as batch:
        batch.add_column(sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("failure_reason", sa.String(255), nullable=True))
    op.create_index("ix_outreach_messages_tenant_sent", "clinic_outreach_messages", ["tenant_id", "sent_at"])


def downgrade() -> None:
    op.drop_index("ix_outreach_messages_tenant_sent", table_name="clinic_outreach_messages")
    with op.batch_alter_table("clinic_outreach_messages") as batch:
        batch.drop_column("failure_reason")
        batch.drop_column("attempts")
        batch.drop_column("sent_at")
