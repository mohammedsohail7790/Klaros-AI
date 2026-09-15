"""Compliance tracking: licenses, insurance policies, bonds, and
certifications with real expiry dates. See app/models/compliance.py.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "licenses",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("license_number", sa.String(100), nullable=True),
        sa.Column("issuing_authority", sa.String(255), nullable=True),
        sa.Column("holder_name", sa.String(255), nullable=True),
        sa.Column("holder_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("document_url", sa.String(1000), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_licenses_tenant_id", "licenses", ["tenant_id"])
    op.create_index("ix_licenses_expiry_date", "licenses", ["expiry_date"])


def downgrade() -> None:
    op.drop_index("ix_licenses_expiry_date", table_name="licenses")
    op.drop_index("ix_licenses_tenant_id", table_name="licenses")
    op.drop_table("licenses")
