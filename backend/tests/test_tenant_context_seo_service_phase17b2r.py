"""Phase 17B-2R: real-PostgreSQL behavioral proof that SEOService's own,
independently-opened sessions (4 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.ai_provider import get_ai_provider
from app.services.seo_service import SEOPageNotFoundError, SEOService

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
async def test_generate_and_publish_page_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.seo_service as seo_service_module

    monkeypatch.setattr(seo_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = SEOService(async_session_maker, get_ai_provider())

    page = await service.generate_page_draft(tenant_id, service="plumbing", location="Austin")
    await service.publish_page(tenant_id, page.id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_publish_tenant_bs_page() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = SEOService(async_session_maker, get_ai_provider())

    page = await service.generate_page_draft(tenant_a, service="roofing", location="Dallas")

    with pytest.raises(SEOPageNotFoundError):
        await service.publish_page(tenant_b, page.id)
