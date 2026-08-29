"""Phase 12C: communication_logs.external_id — the real provider's own
message id (Twilio MessageSid, SendGrid message id), so a later
delivery-status webhook can find and update this exact row.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("communication_logs") as batch_op:
        batch_op.add_column(sa.Column("external_id", sa.String(255), nullable=True))
    op.create_index("ix_communication_logs_external_id", "communication_logs", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_communication_logs_external_id", table_name="communication_logs")
    with op.batch_alter_table("communication_logs") as batch_op:
        batch_op.drop_column("external_id")
