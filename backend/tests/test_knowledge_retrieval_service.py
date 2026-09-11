"""app/services/knowledge_retrieval_service.py — real indexing/search
against the test database (SQLite in this suite; embeddings via the
explicit test-only DeterministicEmbeddingProvider — see
app/services/embedding_provider.py and tests/conftest.py's
EMBEDDING_PROVIDER=deterministic default)."""

import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.knowledge import KnowledgeChunk, KnowledgeFile
from app.services.embedding_provider import NotConfiguredEmbeddingProvider
from app.services.knowledge_retrieval_service import (
    EmbeddingProviderNotConfiguredError,
    KnowledgeFileNotFoundError,
    KnowledgeRetrievalService,
)
from app.services.knowledge_service import KnowledgeService

pytestmark = pytest.mark.asyncio


def _services() -> tuple[KnowledgeService, KnowledgeRetrievalService]:
    ks = KnowledgeService(async_session_maker)
    return ks, KnowledgeRetrievalService(async_session_maker, ks)


async def test_index_file_not_found_raises() -> None:
    _ks, rs = _services()
    with pytest.raises(KnowledgeFileNotFoundError):
        await rs.index_file(uuid.uuid4(), "does/not-exist.md")


async def test_indexing_creates_real_chunks_in_the_database() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    content = " ".join(f"pricingword{i}" for i in range(300))
    await ks.set_file(tenant_id, "office/pricing-rules.md", content, actor_id=None)

    result = await rs.index_file(tenant_id, "office/pricing-rules.md")
    assert result.status == "indexed"
    assert result.chunk_count > 1

    async with async_session_maker() as session:
        file = (
            await session.execute(select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id))
        ).scalar_one()
        chunks = (
            await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.file_id == file.id))
        ).scalars().all()
    assert len(chunks) == result.chunk_count
    assert file.indexed_at is not None
    assert file.content_hash is not None


async def test_reindexing_unchanged_content_is_a_noop() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(tenant_id, "office/service-catalog.md", "Plumbing. Electrical. HVAC.", actor_id=None)
    first = await rs.index_file(tenant_id, "office/service-catalog.md")
    second = await rs.index_file(tenant_id, "office/service-catalog.md")
    assert first.status == "indexed"
    assert second.status == "unchanged"


async def test_changing_content_triggers_reindex_with_fresh_chunks() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(tenant_id, "office/service-catalog.md", "Plumbing services only.", actor_id=None)
    await rs.index_file(tenant_id, "office/service-catalog.md")

    await ks.set_file(tenant_id, "office/service-catalog.md", "Plumbing, electrical, and HVAC services.", actor_id=None)
    result = await rs.index_file(tenant_id, "office/service-catalog.md")
    assert result.status == "indexed"

    async with async_session_maker() as session:
        file = (
            await session.execute(select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id))
        ).scalar_one()
        chunks = (
            await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.file_id == file.id))
        ).scalars().all()
    assert len(chunks) == 1
    assert "electrical" in chunks[0].content.lower()


async def test_deleting_a_file_cascades_to_its_chunks() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(tenant_id, "office/qualification-criteria.md", "Budget over $500 is a good fit.", actor_id=None)
    await rs.index_file(tenant_id, "office/qualification-criteria.md")

    async with async_session_maker() as session:
        file = (
            await session.execute(select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id))
        ).scalar_one()
        file_id = file.id

    await ks.delete_file(tenant_id, "office/qualification-criteria.md", actor_id=None)

    async with async_session_maker() as session:
        remaining = (
            await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id))
        ).scalars().all()
    assert remaining == []


async def test_search_returns_the_most_relevant_chunk_first() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(
        tenant_id, "office/pricing-rules.md",
        "Our plumbing base rate is one hundred twenty dollars per hour with a minimum call-out fee.",
        actor_id=None,
    )
    await ks.set_file(
        tenant_id, "brand/voice-guide.md",
        "We sound friendly, direct, and never use corporate jargon in customer messages.",
        actor_id=None,
    )

    results = await rs.search(tenant_id, "plumbing hourly rate pricing", top_k=5, score_threshold=0.0)
    assert results
    assert results[0].file_path == "office/pricing-rules.md"


async def test_search_respects_top_k() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    for i in range(5):
        await ks.set_file(tenant_id, f"office/doc-{i}.md", f"Content about topic {i} plumbing electrical", actor_id=None)

    results = await rs.search(tenant_id, "plumbing electrical topic", top_k=2, score_threshold=0.0)
    assert len(results) <= 2


async def test_search_respects_score_threshold() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(tenant_id, "brand/voice-guide.md", "completely unrelated content about zebras and giraffes", actor_id=None)

    results = await rs.search(tenant_id, "plumbing pricing rate", top_k=5, score_threshold=0.99)
    assert results == []


async def test_search_path_prefix_filters_by_category() -> None:
    tenant_id = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(tenant_id, "office/pricing-rules.md", "plumbing rate pricing information here", actor_id=None)
    await ks.set_file(tenant_id, "brand/voice-guide.md", "plumbing rate pricing information here too", actor_id=None)

    results = await rs.search(tenant_id, "plumbing rate pricing", top_k=5, score_threshold=0.0, path_prefix="office/")
    assert results
    assert all(r.file_path.startswith("office/") for r in results)


async def test_search_never_returns_another_tenants_chunks() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ks, rs = _services()
    await ks.set_file(tenant_a, "office/pricing-rules.md", "tenant A plumbing pricing details here", actor_id=None)

    results = await rs.search(tenant_b, "tenant A plumbing pricing details", top_k=5, score_threshold=0.0)
    assert results == []


async def test_search_raises_when_embedding_provider_not_configured() -> None:
    tenant_id = uuid.uuid4()
    ks = KnowledgeService(async_session_maker)
    rs = KnowledgeRetrievalService(async_session_maker, ks, embedding_provider=NotConfiguredEmbeddingProvider())
    await ks.set_file(tenant_id, "office/pricing-rules.md", "some content", actor_id=None)

    with pytest.raises(EmbeddingProviderNotConfiguredError):
        await rs.search(tenant_id, "some query")


async def test_indexing_skips_when_embedding_provider_not_configured() -> None:
    tenant_id = uuid.uuid4()
    ks = KnowledgeService(async_session_maker)
    rs = KnowledgeRetrievalService(async_session_maker, ks, embedding_provider=NotConfiguredEmbeddingProvider())
    await ks.set_file(tenant_id, "office/pricing-rules.md", "some content", actor_id=None)

    result = await rs.index_file(tenant_id, "office/pricing-rules.md")
    assert result.status == "skipped_no_provider"
    assert result.chunk_count == 0
