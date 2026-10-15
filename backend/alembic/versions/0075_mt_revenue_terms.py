"""Clinic commercial terms, and the invoice raised for a commission.

Additive only: one new table (`medical_tourism_provider_terms`, row-level security on PostgreSQL like 0072-0074) and one new nullable column (`referral_commissions.invoice_id`).
Nothing existing changes meaning: a clinic with no terms behaves exactly as before.

Rollback: `alembic downgrade 0074_site_hosting` drops the table and the column (terms entered and the commission-to-invoice links are lost; export them first if they matter).

Revision ID: 0075_mt_revenue_terms
Revises: 0074_site_hosting
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_mt_revenue_terms"
down_revision: Union[str, None] = "0074_site_hosting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLE = "medical_tourism_provider_terms"
_COMMISSIONS = "medical_tourism_referral_commissions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("provider_id", _UUID, sa.ForeignKey("medical_tourism_providers.id"), nullable=False),
        sa.Column("commission_basis", sa.String(20), nullable=True),
        sa.Column("commission_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("flat_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("qualifying_outcome", sa.String(32), nullable=True),
        sa.Column("agreement_reference", sa.String(255), nullable=True),
        sa.Column("updated_by", _UUID, nullable=True),
        sa.UniqueConstraint("tenant_id", "provider_id", name="uq_mt_provider_terms"),
    )
    op.create_index("ix_mt_provider_terms_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_mt_provider_terms_provider_id", _TABLE, ["provider_id"])
    op.add_column(_COMMISSIONS, sa.Column("invoice_id", _UUID, nullable=True))

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_select ON {_TABLE} FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_insert ON {_TABLE} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_update ON {_TABLE} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    op.drop_column(_COMMISSIONS, "invoice_id")
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {_TABLE}")
    op.drop_table(_TABLE)
