"""Phase 12D: integration_connections — tenant-scoped connections for
providers where each tenant has their OWN external account (QuickBooks,
Google Calendar, Gmail, Google Ads, Meta Ads, ...), distinct from the
platform-level, single-shared-credential providers wired up in Phase 12C.
Credentials are stored as opaque, Fernet-encrypted ciphertext
(encrypted_credential) — never plaintext.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="NOT_CONNECTED"),
        sa.Column("encrypted_credential", sa.String, nullable=True),
        sa.Column("external_account_id", sa.String(255), nullable=True),
        sa.Column("scopes", sa.String(500), nullable=True),
        sa.Column("connection_metadata", sa.JSON, nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_integration_connections_tenant_provider"),
    )
    op.create_index("ix_integration_connections_tenant_id", "integration_connections", ["tenant_id"])
    op.create_index("ix_integration_connections_provider", "integration_connections", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_integration_connections_provider", table_name="integration_connections")
    op.drop_index("ix_integration_connections_tenant_id", table_name="integration_connections")
    op.drop_table("integration_connections")
