"""Phase 12: upgrade KnowledgeChunk.embedding from a portable JSON float
array to a real PostgreSQL `vector(1536)` column with an HNSW ANN index,
enabling genuine database-side similarity search (`ORDER BY embedding <=>
:query_vector`) instead of loading every tenant-scoped candidate row into
Python for cosine-similarity ranking. 1536 is OpenAIEmbeddingProvider's
real, verified dimension (`text-embedding-3-small` — see
app/models/knowledge.py::EMBEDDING_DIMENSIONS and
app/services/embedding_provider.py). This sandbox's real local
PostgreSQL now has the `vector` extension available (confirmed via
`pg_available_extensions`, version 0.8.0) — the prior migration/model
comments claiming "no reachable PostgreSQL" predate the Phase 7
infrastructure setup and were stale; this migration is the real fix.

HNSW was chosen over IVFFLAT: IVFFLAT needs a representative training
pass (`lists` tuned to table size, and quality degrades until enough rows
exist) which fits a bulk-loaded corpus poorly; this table is populated
incrementally, a few chunks at a time, per-tenant, by
KnowledgeRetrievalService.index_file — HNSW builds correctly with no
training step and remains a fully valid ANN index at any table size,
including empty. Distance/opclass: `vector_cosine_ops`, matching the
`<=>` cosine-distance operator KnowledgeRetrievalService.search() now
uses (this codebase's existing Python `_cosine_similarity` helper is the
same metric — see that function's own docstring history).

Existing data safety: any row whose stored embedding is NULL or is not a
valid 1536-length numeric array cannot be represented in the new
fixed-width column and is deleted before the type conversion — this is
never real user data loss: `KnowledgeChunk` rows are a derived index
artifact regenerated in full by `KnowledgeRetrievalService.index_file`
every time a file is (re)indexed (it deletes and fully rebuilds a file's
chunks, never partially patches them), so a chunk lost here is
transparently rebuilt the next time its owning file is indexed. The
owning `KnowledgeFile.content`/`content_hash`/`indexed_at` are entirely
untouched by this migration. As of this migration being written, both
verification databases (`klaros_test`, `klaros_app`) have zero
`knowledge_chunks` rows, so no actual data was at risk — this cleanup
step exists for correctness in any future non-empty database, not
because it fixed a real problem here.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # This repository's own migration-chain regression test
        # (tests/test_migration_schema_matches_models.py) runs every
        # migration, including this one, against a disposable SQLite
        # file to catch model/migration drift — pgvector DDL is
        # PostgreSQL-only by nature (see app/db/vector_type.py's
        # `PortableVector`, which keeps the `embedding` column as plain
        # JSON on every non-PostgreSQL dialect already), so there is
        # nothing for this migration to do on SQLite: the column is
        # already the correct type for that dialect, unchanged.
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Delete rows that cannot be represented in the new fixed-width
    # column — see this migration's module docstring for why this is
    # always a safe, transparently-recoverable cleanup, never real data
    # loss.
    op.execute(
        f"""
        DELETE FROM knowledge_chunks
        WHERE embedding IS NULL
           OR jsonb_typeof(embedding::jsonb) != 'array'
           OR jsonb_array_length(embedding::jsonb) != {EMBEDDING_DIMENSIONS}
        """
    )

    op.execute(
        f"""
        ALTER TABLE knowledge_chunks
        ALTER COLUMN embedding TYPE vector({EMBEDDING_DIMENSIONS})
        USING embedding::text::vector({EMBEDDING_DIMENSIONS})
        """
    )

    # HNSW, cosine distance — see module docstring for the reasoning.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw_cosine
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw_cosine")
    op.execute(
        """
        ALTER TABLE knowledge_chunks
        ALTER COLUMN embedding TYPE json
        USING to_json(embedding::text)
        """
    )
    # The `vector` extension is intentionally NOT dropped on downgrade —
    # other tenants'/objects' use is out of this migration's scope to
    # reason about, and `DROP EXTENSION` would fail loudly (refusing to
    # proceed) if anything else in the database still depends on it,
    # which is the correct, safe default rather than silently cascading.
