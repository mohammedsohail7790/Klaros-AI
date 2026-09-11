"""Real PostgreSQL concurrency verification for Company Memory's
"at most one ACTIVE row per (tenant_id, key)" invariant (Rule 30):
genuinely concurrent create_memory calls for the SAME key must never both
commit an ACTIVE row — the real partial unique index (migration 0034)
must reject the loser with a genuine IntegrityError, translated into
MemoryConcurrentUpdateError, not just a Python-level check that a race
could slip past.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.company_memory import CompanyMemory, MemoryStatus
from app.services.company_memory_service import CompanyMemoryService, MemoryConcurrentUpdateError

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _service() -> CompanyMemoryService:
    return CompanyMemoryService(async_session_maker)


@requires_real_postgres
async def test_ten_concurrent_writes_for_the_same_key_exactly_one_survives_active() -> None:
    tenant_id = uuid.uuid4()
    service = _service()

    async def write(i: int):
        try:
            return await service.create_memory(
                tenant_id, memory_type="OWNER_PREFERENCE", key="preferred_appointment_time",
                value=f"value-{i}", description=None, source="OWNER_EXPLICIT", created_by=None,
            )
        except MemoryConcurrentUpdateError:
            return None

    results = await asyncio.gather(*[write(i) for i in range(10)])
    successes = [r for r in results if r is not None]
    assert len(successes) >= 1, "at least one concurrent writer must succeed"

    async with async_session_maker() as session:
        active_rows = (
            await session.execute(
                select(CompanyMemory).where(
                    CompanyMemory.tenant_id == tenant_id, CompanyMemory.key == "preferred_appointment_time",
                    CompanyMemory.status == MemoryStatus.ACTIVE,
                )
            )
        ).scalars().all()
    # The real database-level constraint proves this at the row level —
    # never more than one ACTIVE row for this key, regardless of how many
    # writers raced for it.
    assert len(active_rows) == 1


@requires_real_postgres
async def test_concurrent_writes_for_distinct_keys_all_succeed() -> None:
    """The partial unique index must not over-serialize unrelated keys."""
    tenant_id = uuid.uuid4()
    service = _service()

    async def write(key: str):
        return await service.create_memory(
            tenant_id, memory_type="OWNER_PREFERENCE", key=key, value="x", description=None,
            source="OWNER_EXPLICIT", created_by=None,
        )

    keys = [f"preference_{i}" for i in range(8)]
    results = await asyncio.gather(*[write(k) for k in keys])
    assert all(r.status == MemoryStatus.ACTIVE for r in results)
    assert len({r.id for r in results}) == 8


@requires_real_postgres
async def test_concurrent_writes_for_different_tenants_never_contend() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = _service()

    async def write(tenant_id):
        return await service.create_memory(
            tenant_id, memory_type="OWNER_PREFERENCE", key="preferred_appointment_time", value="morning",
            description=None, source="OWNER_EXPLICIT", created_by=None,
        )

    results = await asyncio.gather(write(tenant_a), write(tenant_b))
    assert all(r.status == MemoryStatus.ACTIVE for r in results)
    assert len({r.id for r in results}) == 2
