"""Phase 15: quote deposit collection — quotes.deposit_type/deposit_value/
deposit_amount (deposit configuration + the frozen amount actually due,
set once at accept time) and payments.quote_id (links a deposit Payment
directly to its Quote — no Invoice exists yet at deposit time, so the
existing PaymentAllocation(invoice_id) path doesn't apply). Both sets of
columns are nullable/unset for every pre-Phase-15 row.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("quotes") as batch_op:
        batch_op.add_column(sa.Column("deposit_type", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("deposit_value", sa.Numeric(14, 2), nullable=True))
        batch_op.add_column(sa.Column("deposit_amount", sa.Numeric(14, 2), nullable=True))

    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(sa.Column("quote_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_index("ix_payments_quote_id", ["quote_id"])


def downgrade() -> None:
    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_index("ix_payments_quote_id")
        batch_op.drop_column("quote_id")

    with op.batch_alter_table("quotes") as batch_op:
        batch_op.drop_column("deposit_amount")
        batch_op.drop_column("deposit_value")
        batch_op.drop_column("deposit_type")
