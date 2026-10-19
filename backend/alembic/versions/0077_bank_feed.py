"""Bank feed: bank accounts, bank transactions and the sync cursor (Plaid).

Additive only: three new tables, all tenant-scoped with row-level security on PostgreSQL (tenant_id = current_tenant_id()) like 0072-0076. `bank_transactions` is unique per
(tenant_id, external_transaction_id) so a repeated sync never duplicates a row. Amounts are positive `Numeric(14, 2)` with a separate `direction` (IN/OUT).

Rollback: `alembic downgrade 0076_inbound_email` drops all three tables (the stored feed and the match records are lost; the bank can be re-synced from Plaid).

Revision ID: 0077_bank_feed
Revises: 0076_inbound_email
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0077_bank_feed"
down_revision: Union[str, None] = "0076_inbound_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLES = ("bank_accounts", "bank_transactions", "bank_feed_state")


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "bank_accounts", *_base(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mask", sa.String(10), nullable=True),
        sa.Column("type", sa.String(40), nullable=True),
        sa.Column("subtype", sa.String(40), nullable=True),
        sa.Column("current_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "provider", "external_account_id", name="uq_bank_account_external"),
    )
    op.create_index("ix_bank_accounts_tenant_id", "bank_accounts", ["tenant_id"])
    op.create_table(
        "bank_transactions", *_base(),
        sa.Column("bank_account_id", _UUID, nullable=False),
        sa.Column("external_transaction_id", sa.String(255), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("merchant", sa.String(255), nullable=True),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("match_status", sa.String(12), nullable=False),
        sa.Column("matched_entity_type", sa.String(20), nullable=True),
        sa.Column("matched_entity_id", _UUID, nullable=True),
        sa.Column("match_method", sa.String(10), nullable=True),
        sa.Column("match_candidates", sa.JSON(), nullable=True),
        sa.UniqueConstraint("tenant_id", "external_transaction_id", name="uq_bank_transaction_external"),
    )
    op.create_index("ix_bank_transactions_tenant_id", "bank_transactions", ["tenant_id"])
    op.create_index("ix_bank_transactions_bank_account_id", "bank_transactions", ["bank_account_id"])
    op.create_index("ix_bank_transactions_posted_date", "bank_transactions", ["posted_date"])
    op.create_index("ix_bank_transactions_match_status", "bank_transactions", ["match_status"])
    op.create_table(
        "bank_feed_state", *_base(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(30), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_bank_feed_state_provider"),
    )
    op.create_index("ix_bank_feed_state_tenant_id", "bank_feed_state", ["tenant_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_select ON {table} FOR SELECT USING (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_delete ON {table} FOR DELETE USING (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            for policy in ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete"):
                op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.drop_table("bank_feed_state")
    op.drop_table("bank_transactions")
    op.drop_table("bank_accounts")
