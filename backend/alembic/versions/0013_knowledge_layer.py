"""Phase 12: the Company-OS Knowledge Layer — knowledge_files, a
DB-backed, tenant-scoped, path-addressed markdown document store.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_files",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "path", name="uq_knowledge_file_path"),
    )
    op.create_index("ix_knowledge_files_tenant_id", "knowledge_files", ["tenant_id"])
    op.create_index("ix_knowledge_files_path", "knowledge_files", ["path"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_files_path", table_name="knowledge_files")
    op.drop_index("ix_knowledge_files_tenant_id", table_name="knowledge_files")
    op.drop_table("knowledge_files")
