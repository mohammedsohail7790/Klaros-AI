"""Clinic outreach campaigns: three tenant-scoped tables (campaign settings, one row per clinic per campaign, suppression list).

Additive only. All three have row-level security on PostgreSQL (tenant_id = current_tenant_id()) like 0072-0076.

Rollback: `alembic downgrade 0079_catalog_real_connectors` drops all three tables (outreach state and suppressions are lost; export them first if they matter).

Revision ID: 0080_clinic_outreach
Revises: 0079_catalog_real_connectors
Create Date: 2026-10-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0080_clinic_outreach"
down_revision: Union[str, None] = "0079_catalog_real_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_TABLES = ("clinic_outreach_campaigns", "clinic_outreach", "outreach_suppressions")


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "clinic_outreach_campaigns", *_base(),
        sa.Column("campaign_id", _UUID, sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("term_sheet", sa.JSON(), nullable=True),
        sa.Column("sender", sa.JSON(), nullable=True),
        sa.Column("limits", sa.JSON(), nullable=True),
        sa.Column("countries", sa.JSON(), nullable=True),
        sa.Column("live_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shadow_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_checks", sa.JSON(), nullable=True),
        sa.UniqueConstraint("campaign_id"),
    )
    op.create_index("ix_clinic_outreach_campaigns_tenant_id", "clinic_outreach_campaigns", ["tenant_id"])
    op.create_table(
        "clinic_outreach", *_base(),
        sa.Column("campaign_id", _UUID, sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("provider_id", _UUID, sa.ForeignKey("medical_tourism_providers.id"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("contact_source", sa.String(10), nullable=True),
        sa.Column("contact_source_url", sa.String(2000), nullable=True),
        sa.Column("contact_found_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contact_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emails_sent", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handoff_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("campaign_id", "provider_id", name="uq_clinic_outreach_campaign_provider"),
    )
    op.create_index("ix_clinic_outreach_tenant_id", "clinic_outreach", ["tenant_id"])
    op.create_index("ix_clinic_outreach_campaign_id", "clinic_outreach", ["campaign_id"])
    op.create_index("ix_clinic_outreach_provider_id", "clinic_outreach", ["provider_id"])
    op.create_index("ix_clinic_outreach_stage", "clinic_outreach", ["stage"])
    op.create_table(
        "outreach_suppressions", *_base(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "value", name="uq_outreach_suppression_value"),
    )
    op.create_index("ix_outreach_suppressions_tenant_id", "outreach_suppressions", ["tenant_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_select ON {table} FOR SELECT USING (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            for policy in ("tenant_select", "tenant_insert", "tenant_update"):
                op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.drop_table("outreach_suppressions")
    op.drop_table("clinic_outreach")
    op.drop_table("clinic_outreach_campaigns")
