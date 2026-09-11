"""The sales-contract lifecycle — genuinely absent before this. Distinct
from `CustomerSignoff` (post-completion job signoff, a different business
moment) and from Quote's own ACCEPTED/DECLINED decision. One Contract per
Quote, created automatically once a quote is accepted (see
app/events/finance_handlers.py's QUOTE_ACCEPTED subscriber).

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
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
        "contracts",
        *_timestamp_cols(),
        sa.Column("contract_number", sa.String(50), nullable=False),
        sa.Column("quote_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signer_name", sa.String(200), nullable=True),
        sa.Column("signer_email", sa.String(255), nullable=True),
        sa.Column("decline_reason", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "contract_number", name="uq_contracts_tenant_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_contracts_tenant_idempotency_key"),
    )
    op.create_index("ix_contracts_tenant_id", "contracts", ["tenant_id"])
    op.create_index("ix_contracts_quote_id", "contracts", ["quote_id"])
    op.create_index("ix_contracts_customer_id", "contracts", ["customer_id"])
    op.create_index("ix_contracts_status", "contracts", ["status"])
    op.create_index("ix_contracts_idempotency_key", "contracts", ["idempotency_key"])


def downgrade() -> None:
    op.drop_index("ix_contracts_idempotency_key", table_name="contracts")
    op.drop_index("ix_contracts_status", table_name="contracts")
    op.drop_index("ix_contracts_customer_id", table_name="contracts")
    op.drop_index("ix_contracts_quote_id", table_name="contracts")
    op.drop_index("ix_contracts_tenant_id", table_name="contracts")
    op.drop_table("contracts")
