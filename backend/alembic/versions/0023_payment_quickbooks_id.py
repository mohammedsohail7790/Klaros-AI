"""Phase 17: payments.quickbooks_payment_id — the QuickBooks Payment a
Klaros Payment has been synced to, once it has been. A separate slot from
the existing payments.provider/external_id (which already identify the
ORIGINATING provider, e.g. Stripe) — mirrors the same "secondary
accounting-sync target" relationship Invoice already has with QuickBooks
via its own external_provider/external_id. Nullable/unset for every
pre-Phase-17 row and for every payment never synced to QuickBooks.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(sa.Column("quickbooks_payment_id", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_column("quickbooks_payment_id")
