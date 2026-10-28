"""Clinic quotes: what each clinic quoted, in writing, for one patient's enquiry.

Additive only: one new table. Row-level security on PostgreSQL as in 0073 (tenant_id = current_tenant_id()), with an update policy because a quote can be superseded or withdrawn.

Rollback: `alembic downgrade 0086_dropshipping_pay_reminders` drops the table and its policies (recorded quotes are lost; export them first if they matter).

Revision ID: 0087_clinic_quotes
Revises: 0086_dropshipping_pay_reminders
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0087_clinic_quotes"
down_revision: Union[str, None] = "0086_dropshipping_pay_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLE = "medical_tourism_clinic_quotes"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("lead_id", _UUID, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("provider_id", _UUID, sa.ForeignKey("medical_tourism_providers.id"), nullable=False),
        sa.Column("procedure_id", _UUID, sa.ForeignKey("medical_tourism_procedures.id"), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("includes", sa.Text(), nullable=False, server_default=""),
        sa.Column("excludes", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("recorded_by", _UUID, nullable=True),
    )
    op.create_index(f"ix_{_TABLE}_tenant_id", _TABLE, ["tenant_id"])
    op.create_index(f"ix_{_TABLE}_lead_id", _TABLE, ["lead_id"])
    op.create_index(f"ix_{_TABLE}_provider_id", _TABLE, ["provider_id"])
    op.create_index(f"ix_{_TABLE}_status", _TABLE, ["status"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_select ON {_TABLE} FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_insert ON {_TABLE} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute(f"CREATE POLICY tenant_update ON {_TABLE} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {_TABLE}")
    for ix in ("status", "provider_id", "lead_id", "tenant_id"):
        op.drop_index(f"ix_{_TABLE}_{ix}", table_name=_TABLE)
    op.drop_table(_TABLE)
