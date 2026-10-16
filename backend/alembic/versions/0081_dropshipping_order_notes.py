"""Dropshipping orders, branch 3: payment reference/source and lifecycle columns on orders, a note on shipments.

Additive only: seven nullable columns, no data change. The row-level security policies of 0080 already cover both tables (they filter on tenant_id).
  * dropshipping_orders:    payment_reference, payment_source (STRIPE | MANUAL), status_note, cancelled_at, delivered_at, personal_data_erased_at
  * dropshipping_shipments: note

Rollback: `alembic downgrade 0080_dropshipping_store` drops the seven columns (payment references and notes entered since are lost: export them first if they matter).

Revision ID: 0081_dropshipping_order_notes
Revises: 0080_dropshipping_store
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0081_dropshipping_order_notes"
down_revision: Union[str, None] = "0080_dropshipping_store"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORDER_COLUMNS = (
    ("payment_reference", sa.String(160)),
    ("payment_source", sa.String(12)),
    ("status_note", sa.Text()),
    ("cancelled_at", sa.DateTime(timezone=True)),
    ("delivered_at", sa.DateTime(timezone=True)),
    ("personal_data_erased_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    with op.batch_alter_table("dropshipping_orders") as batch:
        for name, type_ in _ORDER_COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))
    with op.batch_alter_table("dropshipping_shipments") as batch:
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dropshipping_shipments") as batch:
        batch.drop_column("note")
    with op.batch_alter_table("dropshipping_orders") as batch:
        for name, _type in reversed(_ORDER_COLUMNS):
            batch.drop_column(name)
