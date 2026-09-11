"""Phase 3 (RAG): chunking, embedding-backed indexing, and tenant-scoped
semantic search over the existing Company-OS Knowledge Layer
(KnowledgeFile — see app/services/knowledge_service.py, which this service
uses for file access rather than duplicating it).

Indexing is idempotent: `index_file` recomputes the file's content hash
and only re-chunks/re-embeds when it has actually changed, so re-running
it (e.g. after every `knowledge.set_file`) is cheap and never produces
duplicate chunks.

Tenant isolation: every chunk fetch is scoped by `tenant_id` in the SQL
WHERE clause BEFORE any similarity computation runs — no cross-tenant row
is ever fetched, let alone scored, in the first place.

Phase 12: similarity RANKING is now performed by PostgreSQL itself
(`ORDER BY embedding <=> :query_vector`, HNSW-indexed — see migration
0032 and app/models/knowledge.py::KnowledgeChunk) when running against a
real PostgreSQL database. The original Python-side cosine-similarity
ranking is kept, unchanged, as the SQLite code path (`pgvector` cannot
exist there — see _search_python_cosine / _search_postgres_vector
below), so the existing SQLite-backed test suite keeps working exactly
as it always has.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.models.knowledge import KnowledgeChunk, KnowledgeFile
from app.services.embedding_provider import EmbeddingProvider, get_embedding_provider
from app.services.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)


class KnowledgeFileNotFoundError(Exception):
    pass


class EmbeddingProviderNotConfiguredError(Exception):
    pass


def chunk_text(content: str, *, chunk_size: int, overlap: int) -> list[str]:
    """Deterministic, word-boundary-aware fixed-size chunking with
    overlap. Same input always produces the same chunks in the same
    order — proven by tests/test_knowledge_chunking.py. Always makes
    forward progress (even if `overlap >= chunk_size`), so it can never
    loop indefinitely on a misconfigured chunk size."""
    words = content.split()
    if not words:
        return []

    chunks: list[str] = []
    n = len(words)
    start = 0
    while start < n:
        current: list[str] = []
        length = 0
        end = start
        while end < n:
            word = words[end]
            add_len = len(word) + (1 if current else 0)
            if current and length + add_len > chunk_size:
                break
            current.append(word)
            length += add_len
            end += 1
        chunks.append(" ".join(current))
        if end >= n:
            break

        # Step the next chunk's start back into this one's tail by
        # roughly `overlap` characters, but never fail to advance.
        new_start = end
        overlap_len = 0
        while new_start > start and overlap_len < overlap:
            new_start -= 1
            overlap_len += len(words[new_start]) + 1
        start = new_start if new_start > start else end
    return chunks


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class SearchResult:
    file_path: str
    chunk_index: int
    content: str
    score: float
    updated_at: datetime


@dataclass
class IndexResult:
    file_path: str
    status: str  # "indexed" | "unchanged" | "skipped_no_provider"
    chunk_count: int


class KnowledgeRetrievalService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        knowledge_service: KnowledgeService,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._knowledge_service = knowledge_service
        self._embedding_provider = embedding_provider or get_embedding_provider()

    async def index_file(self, tenant_id: uuid.UUID, path: str) -> IndexResult:
        file = await self._knowledge_service.get_file(tenant_id, path)
        if file is None:
            raise KnowledgeFileNotFoundError(f"Knowledge file not found: {path}")

        content_hash = hashlib.sha256(file.content.encode()).hexdigest()
        if file.content_hash == content_hash and file.indexed_at is not None:
            logger.info("knowledge_index_unchanged", tenant_id=str(tenant_id), path=path)
            return IndexResult(file_path=path, status="unchanged", chunk_count=0)

        if not self._embedding_provider.is_connected:
            logger.warning(
                "knowledge_index_skipped_no_provider", tenant_id=str(tenant_id), path=path,
                provider=self._embedding_provider.name,
            )
            return IndexResult(file_path=path, status="skipped_no_provider", chunk_count=0)

        settings = get_settings()
        chunks = chunk_text(
            file.content,
            chunk_size=settings.KNOWLEDGE_CHUNK_SIZE_CHARS,
            overlap=settings.KNOWLEDGE_CHUNK_OVERLAP_CHARS,
        )

        vectors: list[list[float]] = []
        if chunks:
            vectors, outcome = await self._embedding_provider.embed_batch(chunks)
            if vectors is None:
                logger.error(
                    "knowledge_index_embedding_failed", tenant_id=str(tenant_id), path=path,
                    error=outcome.error_detail,
                )
                raise EmbeddingProviderNotConfiguredError(
                    f"embedding call failed: {outcome.error_detail}"
                )

        async with self._session_factory() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.file_id == file.id))
            for index, (chunk_content, vector) in enumerate(zip(chunks, vectors)):
                session.add(
                    KnowledgeChunk(
                        tenant_id=tenant_id,
                        file_id=file.id,
                        chunk_index=index,
                        content=chunk_content,
                        embedding=vector,
                        embedding_model=self._embedding_provider.model_name,
                    )
                )
            db_file = (
                await session.execute(select(KnowledgeFile).where(KnowledgeFile.id == file.id))
            ).scalar_one()
            db_file.content_hash = content_hash
            db_file.indexed_at = datetime.now(timezone.utc)
            await session.commit()

        logger.info(
            "knowledge_index_completed", tenant_id=str(tenant_id), path=path, chunk_count=len(chunks),
            provider=self._embedding_provider.name,
        )
        return IndexResult(file_path=path, status="indexed", chunk_count=len(chunks))

    async def ensure_all_indexed(self, tenant_id: uuid.UUID) -> list[IndexResult]:
        """Indexes every knowledge file that is new or has changed since
        its last index — cheap to call before every search (unchanged
        files are a single hash comparison, no re-embedding, no DB
        write) so search results never reflect stale content without
        requiring a separate manual "reindex" step."""
        files = await self._knowledge_service.list_files(tenant_id)
        return [await self.index_file(tenant_id, f.path) for f in files]

    async def search(
        self,
        tenant_id: uuid.UUID,
        query: str,
        *,
        top_k: int | None = None,
        score_threshold: float | None = None,
        path_prefix: str | None = None,
    ) -> list[SearchResult]:
        settings = get_settings()
        top_k = top_k if top_k is not None else settings.KNOWLEDGE_SEARCH_TOP_K
        # Rule 12: server-side bound, regardless of what a caller/frontend
        # requests — never trust a client-supplied limit directly into a
        # query. 200 is far beyond any real UI use case for this feature.
        top_k = max(1, min(top_k, 200))
        score_threshold = (
            score_threshold if score_threshold is not None else settings.KNOWLEDGE_SEARCH_SCORE_THRESHOLD
        )

        if not self._embedding_provider.is_connected:
            logger.warning("knowledge_search_no_provider", tenant_id=str(tenant_id))
            raise EmbeddingProviderNotConfiguredError("no embedding provider configured")

        await self.ensure_all_indexed(tenant_id)

        query_vector, outcome = await self._embedding_provider.embed(query)
        if query_vector is None:
            logger.error("knowledge_search_embedding_failed", tenant_id=str(tenant_id), error=outcome.error_detail)
            raise EmbeddingProviderNotConfiguredError(f"embedding call failed: {outcome.error_detail}")

        async with self._session_factory() as session:
            is_postgres = session.bind is not None and session.bind.dialect.name == "postgresql"
            if is_postgres:
                results = await self._search_postgres_vector(
                    session, tenant_id, query_vector, top_k=top_k, score_threshold=score_threshold,
                    path_prefix=path_prefix,
                )
            else:
                results = await self._search_python_cosine(
                    session, tenant_id, query_vector, top_k=top_k, score_threshold=score_threshold,
                    path_prefix=path_prefix,
                )

        logger.info(
            "knowledge_search_completed", tenant_id=str(tenant_id), top_k=top_k, path_prefix=path_prefix,
            result_count=len(results), backend="postgres_pgvector" if is_postgres else "python_cosine",
        )
        return results

    async def _search_postgres_vector(
        self, session, tenant_id: uuid.UUID, query_vector: list[float], *, top_k: int,
        score_threshold: float, path_prefix: str | None,
    ) -> list[SearchResult]:
        """Real database-side ANN similarity search — PostgreSQL performs
        the ranking (`ORDER BY embedding <=> :query_vector`, HNSW-indexed,
        see migration 0032), never a full tenant-scoped table scan
        re-ranked in Python. Tenant isolation is still the very first
        predicate in the WHERE clause, exactly as before."""
        distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
        # pgvector's cosine_distance = 1 - cosine_similarity for the same
        # metric this service always used — converting the existing
        # similarity-based score_threshold into the equivalent distance
        # bound keeps the public score semantics (higher = better,
        # 1.0 = identical) unchanged for every caller.
        distance_threshold = 1.0 - score_threshold
        stmt = (
            select(KnowledgeChunk, KnowledgeFile.path, KnowledgeFile.updated_at, distance.label("distance"))
            .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
            .where(KnowledgeChunk.tenant_id == tenant_id, distance <= distance_threshold)
        )
        if path_prefix:
            stmt = stmt.where(KnowledgeFile.path.like(f"{path_prefix}%"))
        stmt = stmt.order_by(distance.asc(), KnowledgeFile.path.asc(), KnowledgeChunk.chunk_index.asc()).limit(top_k)

        rows = (await session.execute(stmt)).all()
        return [
            SearchResult(
                file_path=path, chunk_index=chunk.chunk_index, content=chunk.content,
                score=1.0 - float(dist), updated_at=updated_at,
            )
            for chunk, path, updated_at, dist in rows
        ]

    async def _search_python_cosine(
        self, session, tenant_id: uuid.UUID, query_vector: list[float], *, top_k: int,
        score_threshold: float, path_prefix: str | None,
    ) -> list[SearchResult]:
        """The original Python-side ranking — kept as the SQLite code
        path (pgvector cannot exist there), unchanged in behavior from
        before the Phase 12 pgvector upgrade. Tenant isolation is
        enforced HERE, in the SQL WHERE clause — every row this query can
        ever return already belongs to tenant_id before any Python code
        runs."""
        stmt = (
            select(KnowledgeChunk, KnowledgeFile.path, KnowledgeFile.updated_at)
            .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
            .where(KnowledgeChunk.tenant_id == tenant_id)
        )
        if path_prefix:
            stmt = stmt.where(KnowledgeFile.path.like(f"{path_prefix}%"))
        rows = (await session.execute(stmt)).all()

        scored = [
            SearchResult(
                file_path=path, chunk_index=chunk.chunk_index, content=chunk.content,
                score=_cosine_similarity(query_vector, chunk.embedding), updated_at=updated_at,
            )
            for chunk, path, updated_at in rows
            if len(chunk.embedding) == len(query_vector)  # skip chunks embedded by an incompatible model/dim
        ]
        scored.sort(key=lambda r: (-r.score, r.file_path, r.chunk_index))
        return [r for r in scored if r.score >= score_threshold][:top_k]

    async def delete_file_index(self, tenant_id: uuid.UUID, file_id: uuid.UUID) -> None:
        """Explicit cleanup helper — in practice the FK's ON DELETE CASCADE
        already removes chunks when a KnowledgeFile row is deleted; this
        exists for callers that need to re-index without deleting the file
        itself (e.g. before a full re-chunk)."""
        async with self._session_factory() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id))
            await session.commit()
