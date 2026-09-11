"""Phase 3 (RAG/knowledge retrieval): adds the chunk/embedding storage the
knowledge layer needs for semantic retrieval, plus indexing-state columns
on knowledge_files so re-indexing can be idempotent (unchanged content ==
no re-embed). See app/services/knowledge_retrieval_service.py and
ARCHITECTURE_TRACEABILITY.md for why `embedding` is a portable JSON float
array rather than a native pgvector column: this sandbox has no reachable
PostgreSQL to build/verify a real pgvector column or ANN index against.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("knowledge_files", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("knowledge_files", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "file_id", sa.Uuid(as_uuid=True), sa.ForeignKey("knowledge_files.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.UniqueConstraint("file_id", "chunk_index", name="uq_knowledge_chunk_file_index"),
    )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_file_id", "knowledge_chunks", ["file_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_file_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_tenant_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_column("knowledge_files", "indexed_at")
    op.drop_column("knowledge_files", "content_hash")
