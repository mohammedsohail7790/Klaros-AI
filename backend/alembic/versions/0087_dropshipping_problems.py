"""Dropshipping: a shopper's report that something is wrong with an order they received (damaged, wrong or missing items).

Additive only: one new table, dropshipping_problem_reports (one row per report: the order, what the shopper wrote, PENDING / APPROVED / REJECTED, and the staff decision), with the
same row-level security as the other store tables (a tenant sees only its own rows; rows can be added and decided but never deleted).

Rollback: `alembic downgrade 0086_dropshipping_pay_reminders` drops the table (the reports are lost).

Revision ID: 0087_dropshipping_problems
Revises: 0086_dropshipping_pay_reminders
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0087_dropshipping_problems"
down_revision: Union[str, None] = "0086_dropshipping_pay_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLE = "dropshipping_problem_reports"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("order_id", _UUID, sa.ForeignKey("dropshipping_orders.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="PENDING"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", _UUID, nullable=True),
    )
    op.create_index("ix_dropshipping_problem_reports_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_dropshipping_problem_reports_order_id", _TABLE, ["order_id"])
    op.create_index("ix_ds_problem_reports_tenant_status", _TABLE, ["tenant_id", "status"])
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
    op.drop_index("ix_ds_problem_reports_tenant_status", table_name=_TABLE)
    op.drop_index("ix_dropshipping_problem_reports_order_id", table_name=_TABLE)
    op.drop_index("ix_dropshipping_problem_reports_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
