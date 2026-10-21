"""Dropshipping: daily stock and price sync settings on suppliers.

Additive only: seven columns on dropshipping_suppliers (feed_url, markup_percent, stock_buffer, max_price_change_percent, sync_enabled, last_sync_at, last_sync_summary).
The new columns have defaults (or are nullable), so existing rows stay valid, and the row-level security policies of 0080 already cover the table.

Rollback: `alembic downgrade 0082_merge_outreach_dropshipping` drops the seven columns (the sync settings and the last run's summary are lost).

Revision ID: 0083_dropshipping_supplier_sync
Revises: 0082_merge_outreach_dropshipping
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083_dropshipping_supplier_sync"
down_revision: Union[str, None] = "0082_merge_outreach_dropshipping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dropshipping_suppliers") as batch:
        batch.add_column(sa.Column("feed_url", sa.String(500), nullable=True))
        batch.add_column(sa.Column("markup_percent", sa.Numeric(6, 2), nullable=True))
        batch.add_column(sa.Column("stock_buffer", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_price_change_percent", sa.Numeric(6, 2), nullable=False, server_default="30"))
        batch.add_column(sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_sync_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dropshipping_suppliers") as batch:
        for name in ("last_sync_summary", "last_sync_at", "sync_enabled", "max_price_change_percent", "stock_buffer", "markup_percent", "feed_url"):
            batch.drop_column(name)
