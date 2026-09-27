"""Phase 17: real PostgreSQL verification that Company Memory + real
pgvector Knowledge retrieval combine safely and correctly inside
KnowledgeQAService's prompt — tenant isolation across BOTH sources at
once, the Phase 13 active-memory uniqueness constraint remains intact,
and pgvector/HNSW retrieval keeps ranking correctly under this new
consumer. No new migration this phase — Alembic head stays at 0034."""

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.services.ai_provider import AICallOutcome
from app.services.company_memory_service import CompanyMemoryService, MemoryConcurrentUpdateError
from app.services.embedding_provider import DeterministicEmbeddingProvider
from app.services.knowledge_qa_service import KnowledgeQAService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_service import KnowledgeService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _CapturingProvider:
    is_connected = True
    name = "fake"
    model = "fake-model-1"

    def __init__(self) -> None:
        self.last_prompt: str | None = None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.last_prompt = prompt
        payload = {"answer": "answer", "sources": ["office/pricing-rules.md"], "answered_from_excerpts": True}
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=json.dumps(payload))


async def _qa_stack(provider) -> tuple[KnowledgeQAService, KnowledgeService, CompanyMemoryService]:
    ks = KnowledgeService(async_session_maker)
    embedding_provider = DeterministicEmbeddingProvider(dimensions=EMBEDDING_DIMENSIONS)
    rs = KnowledgeRetrievalService(async_session_maker, ks, embedding_provider=embedding_provider)
    memory = CompanyMemoryService(async_session_maker)
    qa = KnowledgeQAService(async_session_maker, rs, provider, memory)
    return qa, ks, memory


@requires_real_postgres
async def test_combined_knowledge_and_memory_context_is_tenant_isolated_on_real_postgres() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    provider = _CapturingProvider()
    qa, ks, memory = await _qa_stack(provider)

    await ks.set_file(tenant_a, "office/pricing-rules.md", "Plumbing base rate is $120 per hour, tenant A only.", actor_id=None)
    await ks.set_file(tenant_b, "office/pricing-rules.md", "Plumbing base rate is $999 per hour, tenant B only.", actor_id=None)
    await memory.create_memory(
        tenant_a, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tenant_a_memory_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await memory.create_memory(
        tenant_b, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tenant_b_memory_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )

    await qa.ask(tenant_a, "what is the plumbing rate?", actor_type=ActorType.USER, actor_id=uuid.uuid4())

    assert "tenant a only" in provider.last_prompt.lower()
    assert "tenant b only" not in provider.last_prompt.lower()
    assert "tenant_a_memory_marker" in provider.last_prompt
    assert "tenant_b_memory_marker" not in provider.last_prompt


@requires_real_postgres
async def test_pgvector_retrieval_still_ranks_correctly_with_memory_consumer_present() -> None:
    """Proves Phase 17 didn't weaken the Phase 12 real pgvector/HNSW
    ranking path — a real embedding-backed search still returns the most
    relevant chunk first, even now that KnowledgeQAService also queries
    Company Memory."""
    tenant_id = uuid.uuid4()
    provider = _CapturingProvider()
    qa, ks, _memory = await _qa_stack(provider)

    await ks.set_file(tenant_id, "office/plumbing.md", "Plumbing repair and drain cleaning services.", actor_id=None)
    await ks.set_file(tenant_id, "office/electrical.md", "Electrical panel upgrades and wiring services.", actor_id=None)

    result = await qa.ask(tenant_id, "plumbing drain cleaning", actor_type=ActorType.USER, actor_id=uuid.uuid4())
    assert result.available is True
    assert result.citations
    assert result.citations[0].file_path == "office/plumbing.md"

    async with async_session_maker() as session:
        is_postgres = session.bind is not None and session.bind.dialect.name == "postgresql"
    assert is_postgres


@requires_real_postgres
async def test_active_memory_uniqueness_constraint_intact_for_knowledge_qa_consumer() -> None:
    import asyncio

    tenant_id = uuid.uuid4()
    memory = CompanyMemoryService(async_session_maker)

    async def write(i: int):
        try:
            return await memory.create_memory(
                tenant_id, memory_type="OWNER_PREFERENCE", key="brand_tone", value=f"value-{i}",
                description=None, source="OWNER_EXPLICIT", created_by=None,
            )
        except MemoryConcurrentUpdateError:
            return None

    results = await asyncio.gather(*[write(i) for i in range(8)])
    assert len([r for r in results if r is not None]) >= 1

    from sqlalchemy import select

    from app.models.company_memory import CompanyMemory, MemoryStatus

    async with async_session_maker() as session:
        active_rows = (
            await session.execute(
                select(CompanyMemory).where(
                    CompanyMemory.tenant_id == tenant_id, CompanyMemory.key == "brand_tone",
                    CompanyMemory.status == MemoryStatus.ACTIVE,
                )
            )
        ).scalars().all()
    assert len(active_rows) == 1


@requires_real_postgres
async def test_revocation_and_supersession_reach_knowledge_qa_on_real_postgres() -> None:
    tenant_id = uuid.uuid4()
    provider = _CapturingProvider()
    qa, ks, memory = await _qa_stack(provider)
    await ks.set_file(tenant_id, "office/pricing-rules.md", "Plumbing base rate is $120 per hour.", actor_id=None)

    m = await memory.create_memory(
        tenant_id, memory_type="OWNER_PREFERENCE", key="brand_tone", value="pg_temporary_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await qa.ask(tenant_id, "what is the plumbing rate?", actor_type=ActorType.USER, actor_id=uuid.uuid4())
    assert "pg_temporary_marker" in provider.last_prompt

    await memory.revoke_memory(tenant_id, m.id, revoked_by=None, reason="pg test revoke")
    await qa.ask(tenant_id, "what is the plumbing rate, asked independently?", actor_type=ActorType.USER, actor_id=uuid.uuid4())
    assert "pg_temporary_marker" not in provider.last_prompt

    await memory.create_memory(
        tenant_id, memory_type="OWNER_PREFERENCE", key="brand_tone", value="pg_new_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await qa.ask(tenant_id, "what is the plumbing rate, asked again?", actor_type=ActorType.USER, actor_id=uuid.uuid4())
    assert "pg_new_marker" in provider.last_prompt


@requires_real_postgres
async def test_alembic_head_unchanged_this_phase() -> None:
    """Confirms the real database's applied migration matches the
    codebase's live Alembic head (not a hardcoded revision id frozen at
    the phase this test was written in — see the marketing test's
    twin for the full rationale)."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    expected_head = ScriptDirectory.from_config(Config(str(alembic_ini))).get_current_head()

    async with async_session_maker() as session:
        result = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert result == expected_head
