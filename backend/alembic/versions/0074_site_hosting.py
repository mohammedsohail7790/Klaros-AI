"""Uploaded sites and custom domains.

Additive only: three new tables, nothing existing is altered. `uploaded_sites` and `uploaded_site_files` have row-level security on PostgreSQL (tenant_id = current_tenant_id()) like 0066/0071/0072/0073.
`tenant_domains` has NO row-level security on purpose: a visitor's request carries only a hostname, so the hostname -> tenant lookup must work before any tenant is known. It holds no secret.

Rollback: `alembic downgrade 0073_lead_notes` drops all three tables (uploaded files and domain links are lost; export them first if they matter).

Revision ID: 0074_site_hosting
Revises: 0073_lead_notes
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0074_site_hosting"
down_revision: Union[str, None] = "0073_lead_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "uploaded_sites", *_base(),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_uploaded_sites_tenant_id", "uploaded_sites", ["tenant_id"])
    op.create_table(
        "uploaded_site_files", *_base(),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("site_id", _UUID, sa.ForeignKey("uploaded_sites.id"), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )
    op.create_index("ix_uploaded_site_files_tenant_id", "uploaded_site_files", ["tenant_id"])
    op.create_index("ix_uploaded_site_files_site_id", "uploaded_site_files", ["site_id"])
    op.create_table(
        "tenant_domains", *_base(),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("hostname", sa.String(253), nullable=False, unique=True),
        sa.Column("verification_token", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edge_status", sa.String(20), nullable=False, server_default="NOT_AUTOMATED"),
        sa.Column("edge_detail", sa.String(500), nullable=True),
    )
    op.create_index("ix_tenant_domains_tenant_id", "tenant_domains", ["tenant_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("uploaded_sites", "uploaded_site_files"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_select ON {table} FOR SELECT USING (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
        op.execute(f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("uploaded_sites", "uploaded_site_files"):
            for policy in ("tenant_select", "tenant_insert", "tenant_update"):
                op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.drop_table("tenant_domains")
    op.drop_table("uploaded_site_files")
    op.drop_table("uploaded_sites")
