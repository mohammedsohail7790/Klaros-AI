"""Dropshipping: "tell me when it is back" requests for items that are sold out.

Additive only: one new table, dropshipping_stock_alerts (the item, the shopper's e-mail address, and when they were told), with the same row-level security as the other store
tables. The e-mail address is removed once the shopper has been told, so no address is kept longer than it is needed. One request per item and address while it is open.

Rollback: `alembic downgrade 0087_dropshipping_problems` drops the table (the open requests are lost).

Revision ID: 0088_dropshipping_stock_alerts
Revises: 0087_dropshipping_problems
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0088_dropshipping_stock_alerts"
down_revision: Union[str, None] = "0087_dropshipping_problems"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLE = "dropshipping_stock_alerts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("sku_id", _UUID, sa.ForeignKey("dropshipping_skus.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "sku_id", "email", name="uq_ds_stock_alerts_tenant_sku_email"),
    )
    op.create_index("ix_dropshipping_stock_alerts_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_dropshipping_stock_alerts_sku_id", _TABLE, ["sku_id"])
    if op.get_bind().dialect.name != "postgresql":
        return  # row-level security is PostgreSQL-only, as in 0080
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_select ON {_TABLE} FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_insert ON {_TABLE} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_update ON {_TABLE} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {_TABLE}")
    op.drop_index("ix_dropshipping_stock_alerts_sku_id", table_name=_TABLE)
    op.drop_index("ix_dropshipping_stock_alerts_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
