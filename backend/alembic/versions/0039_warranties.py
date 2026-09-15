"""Warranty tracking — a coverage window on a completed job, with an
expiry-based detect sweep for the "warranty check-in" retention
touchpoint. See app/models/retention.py::Warranty.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "warranties",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("item_description", sa.String(500), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("last_checked_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_warranties_tenant_id", "warranties", ["tenant_id"])
    op.create_index("ix_warranties_customer_id", "warranties", ["customer_id"])
    op.create_index("ix_warranties_job_id", "warranties", ["job_id"])
    op.create_index("ix_warranties_expiry_date", "warranties", ["expiry_date"])


def downgrade() -> None:
    op.drop_index("ix_warranties_expiry_date", table_name="warranties")
    op.drop_index("ix_warranties_job_id", table_name="warranties")
    op.drop_index("ix_warranties_customer_id", table_name="warranties")
    op.drop_index("ix_warranties_tenant_id", table_name="warranties")
    op.drop_table("warranties")
