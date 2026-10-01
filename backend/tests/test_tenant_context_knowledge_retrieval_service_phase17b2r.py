"""Phase 17B-2R: real-PostgreSQL behavioral proof that
KnowledgeRetrievalService's own, independently-opened sessions (3 sites)
now stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_service import KnowledgeService

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


def _services() -> tuple[KnowledgeService, KnowledgeRetrievalService]:
    ks = KnowledgeService(async_session_maker)
    return ks, KnowledgeRetrievalService(async_session_maker, ks)


@requires_real_postgres
async def test_index_file_and_search_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.knowledge_retrieval_service as krs_module

    monkeypatch.setattr(krs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    ks, rs = _services()

    await ks.set_file(tenant_id, "office/notes.md", "Our pricing starts at $100 for a standard visit.", actor_id=None)
    result = await rs.index_file(tenant_id, "office/notes.md")
    assert result.status == "indexed"

    await rs.search(tenant_id, "pricing")

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_search_never_returns_tenant_bs_chunks() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    ks, rs = _services()

    await ks.set_file(tenant_a, "office/a-only.md", "This is tenant A's confidential pricing information.", actor_id=None)
    await rs.index_file(tenant_a, "office/a-only.md")

    results_b = await rs.search(tenant_b, "confidential pricing")
    assert results_b == []
