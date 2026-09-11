"""Phase 18: refunds.quickbooks_refund_receipt_id — the QuickBooks
RefundReceipt a Klaros Refund has been synced to, once it has been.
Mirrors payments.quickbooks_payment_id's role exactly (added Phase 17);
Refund had no external-identifier column of any kind before this. NULL
for every pre-Phase-18 row.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("refunds") as batch_op:
        batch_op.add_column(sa.Column("quickbooks_refund_receipt_id", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("refunds") as batch_op:
        batch_op.drop_column("quickbooks_refund_receipt_id")
