"""Phase 17B-2R: real-PostgreSQL behavioral proof for
VerticalExtensionService's 7 session-open sites, genuinely split between
two classifications:
  - `VerticalExtension` itself: GLOBAL/SHARED platform catalog (no
    tenant_id column at all) — create_vertical/get_by_key/list_verticals/
    set_status correctly NEVER call set_tenant_context (proven here by
    asserting the spy is never invoked during those calls).
  - `OrganizationVerticalExtension`: genuinely tenant-scoped (the per-tenant
    opt-in row) — enable_for_organization/list_enabled_for_organization/
    is_enabled_for_organization DO call set_tenant_context (proven here the
    same way as every other service this phase).
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.vertical_extension_service import VerticalExtensionService

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
async def test_global_catalog_methods_never_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.vertical_extension_service as ves_module

    monkeypatch.setattr(ves_module, "set_tenant_context", spy)

    service = VerticalExtensionService(async_session_maker)
    key = f"test_vertical_{uuid.uuid4().hex[:8]}"

    await service.create_vertical(key=key, name="Test Vertical")
    await service.get_by_key(key)
    await service.list_verticals()
    await service.set_status(key, "ACTIVE")

    assert spy.calls == []


@requires_real_postgres
async def test_organization_enablement_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.vertical_extension_service as ves_module

    monkeypatch.setattr(ves_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = VerticalExtensionService(async_session_maker)
    key = f"test_vertical_{uuid.uuid4().hex[:8]}"
    await service.create_vertical(key=key, name="Test Vertical")
    spy.calls.clear()  # isolate to the tenant-scoped calls below

    await service.enable_for_organization(tenant_id, key)
    await service.list_enabled_for_organization(tenant_id)
    assert await service.is_enabled_for_organization(tenant_id, key) is True

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_enablement_invisible_to_tenant_b() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = VerticalExtensionService(async_session_maker)
    key = f"test_vertical_{uuid.uuid4().hex[:8]}"
    await service.create_vertical(key=key, name="Test Vertical")

    await service.enable_for_organization(tenant_a, key)

    assert await service.is_enabled_for_organization(tenant_b, key) is False
    assert await service.list_enabled_for_organization(tenant_b) == []
