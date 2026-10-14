"""Per-tenant settings: one small key/value table, so what a business does is configuration, not code.

Additive only: one new table, nothing existing is altered. Row-level security on PostgreSQL as in 0066/0071 (tenant_id = current_tenant_id()).
Keys in use: `crew` (auto-provision and which crew members are switched off) and `ai_processing` (the AI providers the owner allows for this business's data).

Rollback: `alembic downgrade 0071_app_builder` drops the table and its policies. Tenants then fall back to the platform defaults.

Revision ID: 0072_tenant_settings
Revises: 0071_app_builder
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0072_tenant_settings"
down_revision: Union[str, None] = "0071_app_builder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", _UUID, nullable=True),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_tenant_key"),
    )
    op.create_index("ix_tenant_settings_tenant_id", "tenant_settings", ["tenant_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON tenant_settings FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON tenant_settings FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_update ON tenant_settings FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON tenant_settings")
    op.drop_index("ix_tenant_settings_tenant_id", table_name="tenant_settings")
    op.drop_table("tenant_settings")
