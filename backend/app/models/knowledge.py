"""Phase 12: the Company-OS Knowledge Layer — the file-based source of
truth an Owner (or, going forward, the AI) can read/edit for how their
specific business actually operates: pricing rules, qualification
criteria, brand voice, compliance limits, and so on.

Deliberately DB-backed, not literal files on a local disk: this codebase
runs as a stateless container (or several, behind a load balancer) in
production, and Postgres is already the single source of truth for every
other tenant-scoped record — a local-filesystem "knowledge layer" would
either not survive a redeploy or require its own replicated storage layer
for no real benefit. The `path` convention (`office/pricing-rules.md`,
`brand/voice-guide.md`, ...) is preserved because it's how a human (or the
AI) addresses a document; the storage underneath is a normal table.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin
from app.db.vector_type import PortableVector

# The real production embedding dimension — OpenAIEmbeddingProvider's
# `text-embedding-3-small` (see app/services/embedding_provider.py). The
# PostgreSQL `embedding` column is a fixed-width `vector(EMBEDDING_DIMENSIONS)`
# (Phase 12 migration 0032) so every production row is genuinely ANN-
# indexable; nothing in this codebase ever writes a real (non-test)
# embedding of a different width into this column.
EMBEDDING_DIMENSIONS = 1536


class KnowledgeFile(TenantScopedMixin, Base):
    __tablename__ = "knowledge_files"
    __table_args__ = (UniqueConstraint("tenant_id", "path", name="uq_knowledge_file_path"),)

    # e.g. "office/pricing-rules.md" — category/filename, markdown content.
    path: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # RAG indexing state (added for the knowledge-retrieval layer — see
    # app/services/knowledge_retrieval_service.py). `content_hash` lets
    # indexing be idempotent (skip re-embedding unchanged content);
    # `indexed_at` is None until the file has been chunked/embedded at
    # least once, and is never backdated to fake a real indexing run.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeChunk(TenantScopedMixin, Base):
    """One embedded, retrievable slice of a `KnowledgeFile`'s content —
    see app/services/knowledge_retrieval_service.py for chunking/embedding
    and app/services/embedding_provider.py for what actually produced
    `embedding`.

    `embedding` uses `PortableVector` (app/db/vector_type.py): a real,
    fixed-width `vector(1536)` PostgreSQL column — enabling genuine
    database-side ANN similarity search via an HNSW index (migration
    `0032`) — and a plain JSON float array on every other dialect
    (SQLite, this codebase's default test engine), so the one existing
    test suite built against SQLite keeps working unchanged. Tenant
    ISOLATION is always enforced by the database query's WHERE clause,
    never by fetching cross-tenant rows and filtering them out
    afterward; similarity RANKING is now performed by PostgreSQL itself
    (`ORDER BY embedding <=> :query_vector`) when running against
    PostgreSQL — see KnowledgeRetrievalService.search()."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("file_id", "chunk_index", name="uq_knowledge_chunk_file_index"),
        # Declared here (not just in migration 0032) so it's also created
        # by `Base.metadata.create_all()` — the mechanism the test
        # suite's `_reset_database` fixture uses to build a fresh schema
        # every test, including when DATABASE_URL points at real
        # PostgreSQL for the postgres-specific test files. `postgresql_*`
        # kwargs are ignored on every other dialect (SQLite still gets a
        # plain, harmless index over the column).
        Index(
            "ix_knowledge_chunks_embedding_hnsw_cosine", "embedding",
            postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # ondelete="CASCADE" is real DDL (enforced by PostgreSQL in production);
    # KnowledgeService.delete_file also deletes chunks explicitly, since
    # SQLite (this test suite's engine) only honors ON DELETE CASCADE when
    # `PRAGMA foreign_keys=ON` is set per-connection, which this codebase's
    # engine does not currently do — the explicit delete makes cleanup
    # correct on both engines rather than depending on that pragma.
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(PortableVector(EMBEDDING_DIMENSIONS), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
