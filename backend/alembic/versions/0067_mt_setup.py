"""Medical Tourism one-form setup: one profile row per organisation (`medical_tourism_setup`).

Additive. Holds the owner's setup answers (name, model, patient markets, destinations) and the owner's own confirmations (who/when). No patient data. Real row-level
security on PostgreSQL as in 0065/0066; no DELETE policy.

Revision ID: 0067_mt_setup
Revises: 0066_mt_referrals
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067_mt_setup"
down_revision: Union[str, None] = "0066_mt_referrals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "medical_tourism_setup",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("business_name", sa.String(120), nullable=False),
        sa.Column("business_model", sa.String(16), nullable=False),
        sa.Column("patient_markets", sa.JSON(), nullable=False),
        sa.Column("destinations", sa.JSON(), nullable=False),
        sa.Column("confirmations", sa.JSON(), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_mt_setup_tenant"),
    )
    op.create_index("ix_medical_tourism_setup_tenant_id", "medical_tourism_setup", ["tenant_id"])
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE medical_tourism_setup ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON medical_tourism_setup FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON medical_tourism_setup FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_update ON medical_tourism_setup FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON medical_tourism_setup")
    op.drop_index("ix_medical_tourism_setup_tenant_id", table_name="medical_tourism_setup")
    op.drop_table("medical_tourism_setup")
