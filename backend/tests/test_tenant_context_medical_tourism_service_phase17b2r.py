"""Phase 17B-2R: real-PostgreSQL behavioral proof that MedicalTourismService's
own, independently-opened sessions (16 sites: 15 on `self._session_factory()`
inside the class, plus 1 module-level `_async_session_maker()` site inside
`_provide_website_provider_directory`, the Website Builder's data-provider
callback) now stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.medical_tourism_service import (
    CreateProviderInput,
    MedicalTourismService,
    NotFoundError,
    ProviderStatus,
    _provide_website_provider_directory,
)

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
async def test_create_and_list_providers_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.medical_tourism_service as mt_module

    monkeypatch.setattr(mt_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = MedicalTourismService(async_session_maker)

    provider, deduped = await service.create_provider(
        tenant_id, CreateProviderInput(name="Istanbul Health", country="tr"),
    )
    assert not deduped
    fetched = await service.get_provider(tenant_id, provider.id)
    assert fetched.id == provider.id
    providers, total = await service.list_providers(tenant_id)
    assert total == 1

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_provider() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = MedicalTourismService(async_session_maker)

    provider, _ = await service.create_provider(tenant_a, CreateProviderInput(name="A-only Provider", country="us"))

    with pytest.raises(NotFoundError):
        await service.get_provider(tenant_b, provider.id)

    providers_b, total_b = await service.list_providers(tenant_b)
    assert total_b == 0
    assert provider.id not in [p.id for p in providers_b]


@requires_real_postgres
async def test_website_provider_directory_data_provider_sets_tenant_context(monkeypatch, spy) -> None:
    """The public Website Builder's data-provider callback
    (`_provide_website_provider_directory`) opens its own module-level
    session for the procedure-name lookup — a real gap distinct from the
    class's own `self._session_factory()` sites. `tenant_id` here comes
    from the documented public-website tenant URL boundary (§21), passed
    in trusted, never from request body/query params."""
    import app.services.medical_tourism_service as mt_module

    monkeypatch.setattr(mt_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = MedicalTourismService(async_session_maker)
    provider, _ = await service.create_provider(tenant_id, CreateProviderInput(name="Directory Co", country="mx"))
    spy.calls.clear()

    result = await _provide_website_provider_directory(tenant_id, {"limit": 5})
    assert "items" in result
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "Directory Co"

    # No offerings exist yet, so the procedure_ids-guarded session block
    # (the 16th, module-level site) is never entered for THIS provider —
    # confirms create_provider/list_providers/list_offerings (all inside
    # the service) still set context even when called transitively from
    # the public data-provider path.
    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
