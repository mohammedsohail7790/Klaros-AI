"""Phase 14: quotes, quote_line_items — the pre-work stage the pipeline
was missing (Lead -> Job -> Invoice existed; nothing modeled a formal,
customer-approvable price proposal before work starts). Also adds
jobs.quote_id (nullable — set when a job was created by converting an
accepted quote, null for every job created the pre-Phase-14 way).

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_cols():
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "quotes",
        *_timestamp_cols(),
        sa.Column("quote_number", sa.String(50), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("terms", sa.Text, nullable=True),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "quote_number", name="uq_quotes_tenant_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_quotes_tenant_idempotency_key"),
    )
    op.create_index("ix_quotes_tenant_id", "quotes", ["tenant_id"])
    op.create_index("ix_quotes_customer_id", "quotes", ["customer_id"])
    op.create_index("ix_quotes_lead_id", "quotes", ["lead_id"])
    op.create_index("ix_quotes_job_id", "quotes", ["job_id"])
    op.create_index("ix_quotes_status", "quotes", ["status"])
    op.create_index("ix_quotes_idempotency_key", "quotes", ["idempotency_key"])

    op.create_table(
        "quote_line_items",
        *_timestamp_cols(),
        sa.Column("quote_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_quote_line_items_tenant_id", "quote_line_items", ["tenant_id"])
    op.create_index("ix_quote_line_items_quote_id", "quote_line_items", ["quote_id"])

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("quote_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_index("ix_jobs_quote_id", "jobs", ["quote_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_quote_id", table_name="jobs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("quote_id")

    op.drop_index("ix_quote_line_items_quote_id", table_name="quote_line_items")
    op.drop_index("ix_quote_line_items_tenant_id", table_name="quote_line_items")
    op.drop_table("quote_line_items")

    op.drop_index("ix_quotes_idempotency_key", table_name="quotes")
    op.drop_index("ix_quotes_status", table_name="quotes")
    op.drop_index("ix_quotes_job_id", table_name="quotes")
    op.drop_index("ix_quotes_lead_id", table_name="quotes")
    op.drop_index("ix_quotes_customer_id", table_name="quotes")
    op.drop_index("ix_quotes_tenant_id", table_name="quotes")
    op.drop_table("quotes")
