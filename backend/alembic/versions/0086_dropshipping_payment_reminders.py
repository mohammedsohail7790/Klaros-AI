"""Dropshipping: remember how many payment reminders an unpaid order has had.

Additive only: two columns on dropshipping_orders (payment_reminders_sent, last_payment_reminder_at). Existing rows get 0 and NULL, so nothing is sent for old orders by accident
(the reminder job also ignores orders older than three days), and the row-level security policies of 0080 already cover the table.

Rollback: `alembic downgrade 0085_clinic_outreach_sending` drops the two columns (the count of reminders already sent is lost).

Revision ID: 0086_dropshipping_payment_reminders
Revises: 0085_clinic_outreach_sending
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0086_dropshipping_payment_reminders"
down_revision: Union[str, None] = "0085_clinic_outreach_sending"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dropshipping_orders") as batch:
        batch.add_column(sa.Column("payment_reminders_sent", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_payment_reminder_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dropshipping_orders") as batch:
        batch.drop_column("last_payment_reminder_at")
        batch.drop_column("payment_reminders_sent")
