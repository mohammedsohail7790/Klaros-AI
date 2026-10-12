"""Owner-approved consent wording for public forms.

Additive only. One new table, `consent_wordings`: the exact text a business shows next to its public enquiry form's consent boxes, approved by the owner.
Append-only (select + insert policies only; no update, no delete): a wording is never edited, a new version is approved instead, so consent evidence always
points at the exact words a person saw. Row-level security on PostgreSQL as in 0065 / 0066.

Until now the only way for a wording to count as approved was a value written by hand into the Halla connection's metadata, which no screen or endpoint could
do, so a Medical Tourism business's public enquiry form could never open.

Revision ID: 0070_consent_wordings
Revises: 0069_catalog_plain_copy
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0070_consent_wordings"
down_revision: Union[str, None] = "0069_catalog_plain_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "consent_wordings",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("approved_by", _UUID, nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "version", name="uq_consent_wordings_tenant_version"),
    )
    op.create_index("ix_consent_wordings_tenant_id", "consent_wordings", ["tenant_id"])
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE consent_wordings ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON consent_wordings FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON consent_wordings FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON consent_wordings")
    op.drop_index("ix_consent_wordings_tenant_id", table_name="consent_wordings")
    op.drop_table("consent_wordings")
