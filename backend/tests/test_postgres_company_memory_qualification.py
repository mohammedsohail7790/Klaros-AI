"""Phase 14: real PostgreSQL verification that Company Memory's
tenant-scoped context correctly reaches AIQualificationService's prompt
and never leaks across tenants — and that the Phase 13 partial unique
active-memory constraint (migration 0034) remains intact and enforced
under this new consumer too.
"""

import json
import uuid

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.crm import Lead
from app.services.ai_provider import AICallOutcome, AIProvider
from app.services.ai_qualification_service import AIQualificationService
from app.services.company_memory_service import CompanyMemoryService, MemoryConcurrentUpdateError

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_VALID_RESPONSE = json.dumps(
    {
        "qualification_score": 60, "intent": "inquiry", "urgency": "LOW", "buying_signal": "browsing",
        "summary": "Low-intent inquiry.", "recommended_next_action": "Send informational follow-up.",
    }
)


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


async def _make_lead(tenant_id: uuid.UUID) -> Lead:
    async with async_session_maker() as session:
        lead = Lead(
            tenant_id=tenant_id, name="PG Test Customer", source="WEB", service_requested="Roof inspection",
            urgency="MEDIUM", estimated_value=1200,
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        return lead


@requires_real_postgres
async def test_qualification_memory_context_is_tenant_isolated_on_real_postgres() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    lead_a = await _make_lead(tenant_a)
    memory = CompanyMemoryService(async_session_maker)
    await memory.create_memory(
        tenant_a, memory_type="OWNER_PREFERENCE", key="preferred_lead_type", value="commercial_only_A",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await memory.create_memory(
        tenant_b, memory_type="OWNER_PREFERENCE", key="preferred_lead_type", value="residential_only_B",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )

    provider = _CapturingProvider()
    service = AIQualificationService(async_session_maker, provider)
    await service.generate_recommendation(tenant_a, lead_a.id, actor_type=ActorType.USER, actor_id=uuid.uuid4())

    assert "commercial_only_A" in provider.last_prompt
    assert "residential_only_B" not in provider.last_prompt


@requires_real_postgres
async def test_active_memory_uniqueness_constraint_still_enforced_for_qualification_consumer() -> None:
    """Proves migration 0034's constraint wasn't accidentally weakened by
    this phase's changes — still a real DB-level guarantee, still exactly
    one ACTIVE row per (tenant_id, key), verified with the same key a
    qualification prompt would actually read."""
    import asyncio

    tenant_id = uuid.uuid4()
    memory = CompanyMemoryService(async_session_maker)

    async def write(i: int):
        try:
            return await memory.create_memory(
                tenant_id, memory_type="OWNER_PREFERENCE", key="preferred_lead_type", value=f"value-{i}",
                description=None, source="OWNER_EXPLICIT", created_by=None,
            )
        except MemoryConcurrentUpdateError:
            return None

    results = await asyncio.gather(*[write(i) for i in range(8)])
    successes = [r for r in results if r is not None]
    assert len(successes) >= 1

    from sqlalchemy import select

    from app.models.company_memory import CompanyMemory, MemoryStatus

    async with async_session_maker() as session:
        active_rows = (
            await session.execute(
                select(CompanyMemory).where(
                    CompanyMemory.tenant_id == tenant_id, CompanyMemory.key == "preferred_lead_type",
                    CompanyMemory.status == MemoryStatus.ACTIVE,
                )
            )
        ).scalars().all()
    assert len(active_rows) == 1
