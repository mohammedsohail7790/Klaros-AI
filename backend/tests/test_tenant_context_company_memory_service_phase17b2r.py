"""Phase 17B-2R: real-PostgreSQL behavioral proof that
CompanyMemoryService's own, independently-opened sessions (10 sites) now
stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.company_memory import MemorySource, MemoryType
from app.models.organization import Organization
from app.services.company_memory_service import CompanyMemoryService, MemoryNotFoundError

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


@requires_real_postgres
async def test_create_get_and_list_memory_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.company_memory_service as cms_module

    monkeypatch.setattr(cms_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = CompanyMemoryService(async_session_maker)

    memory = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_contact_hours",
        value="9am-5pm local time", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    await service.get_memory(tenant_id, memory.id)
    await service.list_memories(tenant_id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_memory() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = CompanyMemoryService(async_session_maker)

    memory = await service.create_memory(
        tenant_a, memory_type=MemoryType.OWNER_PREFERENCE, key="a_only_preference",
        value="tenant A only", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )

    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(tenant_b, memory.id)

    memories_b = await service.list_memories(tenant_b)
    assert memory.id not in [m.id for m in memories_b]
