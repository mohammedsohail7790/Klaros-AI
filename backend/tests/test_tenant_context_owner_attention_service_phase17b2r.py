"""Phase 17B-2R: real-PostgreSQL behavioral proof that
OwnerAttentionService's own, independently-opened session (1 site) now
stamps `SET LOCAL app.tenant_id`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Lead, QualificationStatus
from app.models.organization import Organization
from app.services.owner_attention_service import OwnerAttentionService

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


async def _make_org_and_qualified_lead(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(
            Lead(
                tenant_id=tenant_id, name="Attention Test Lead", source="web",
                qualification_status=QualificationStatus.QUALIFIED,
            )
        )
        await session.commit()


@requires_real_postgres
async def test_get_attention_queue_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.owner_attention_service as oas_module

    monkeypatch.setattr(oas_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org_and_qualified_lead(tenant_id)
    service = OwnerAttentionService(async_session_maker)

    items = await service.get_attention_queue(tenant_id)
    assert len(items) >= 1

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_attention_items_never_visible_to_tenant_b() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org_and_qualified_lead(tenant_a)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()

    service = OwnerAttentionService(async_session_maker)
    items_b = await service.get_attention_queue(tenant_b)
    assert items_b == []
