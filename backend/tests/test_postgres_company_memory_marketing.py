"""Phase 15: real PostgreSQL verification that Company Memory's
tenant-scoped context correctly reaches AI-generated marketing content,
and that the Phase 13 partial unique active-memory constraint (migration
0034) remains intact under this new consumer too. No new migration was
needed for this phase — Alembic head stays at 0034.
"""

import json
import uuid

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.operations import Job, JobStatus
from app.services.ai_provider import AICallOutcome, AIProvider
from app.services.company_memory_service import CompanyMemoryService, MemoryConcurrentUpdateError
from app.services.content_service import ContentService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_VALID_RESPONSE = json.dumps({"caption": "Real PostgreSQL caption test."})


class _CapturingProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model-1"

    def __init__(self) -> None:
        self.last_prompt: str | None = None

    async def enrich_brief(self, headline, insights, *, brand_voice=None, company_memory=None):
        return None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.last_prompt = prompt
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=_VALID_RESPONSE)


async def _make_job(tenant_id: uuid.UUID) -> Job:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="PG Marketing Customer")
        session.add(customer)
        await session.flush()
        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, job_number=f"JOB-{uuid.uuid4().hex[:8]}",
            title="Water heater install", service_type="Plumbing", location="Downtown", status=JobStatus.DRAFT,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


@requires_real_postgres
async def test_marketing_memory_context_is_tenant_isolated_on_real_postgres(monkeypatch) -> None:
    monkeypatch.setattr("app.services.content_service.is_llm_connected", lambda: True)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    job_a = await _make_job(tenant_a)
    memory = CompanyMemoryService(async_session_maker)
    await memory.create_memory(
        tenant_a, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tone_only_A",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await memory.create_memory(
        tenant_b, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tone_only_B",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )

    provider = _CapturingProvider()
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = ContentService(async_session_maker, bus, provider)
    await service.generate_draft_from_job(tenant_a, job_a.id, None)

    assert "tone_only_A" in provider.last_prompt
    assert "tone_only_B" not in provider.last_prompt


@requires_real_postgres
async def test_active_memory_uniqueness_constraint_intact_for_marketing_consumer() -> None:
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
async def test_alembic_head_unchanged_this_phase() -> None:
    """No schema change was needed for this phase — confirms the real
    database's applied migration matches the codebase's live Alembic head
    (whatever that head currently is, not a hardcoded revision id frozen
    at the phase this test was written in — the intent is "the DB is
    fully migrated," which a stale literal silently stops verifying every
    time a later phase adds a new migration)."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    expected_head = ScriptDirectory.from_config(Config(str(alembic_ini))).get_current_head()

    async with async_session_maker() as session:
        result = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert result == expected_head
