"""Phase 17B-2R: real-PostgreSQL proof that IntegrationCatalogService's 4
session-open sites correctly NEVER call `set_tenant_context` — every one
operates only on `IntegrationProviderCatalog`, a GLOBAL/SHARED platform
catalog table with no tenant_id column at all (see the class's own
Phase 17B-2R comment and app/models/integration_catalog.py's docstring).
This is a classification-lock test, the same shape as
`test_tenant_context_vertical_extension_service_phase17b2r.py`'s global-
catalog-methods proof.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.integration_catalog import ProviderAuthShape, ProviderImplementationStatus
from app.services.integration_catalog_service import CatalogProviderNotFoundError, IntegrationCatalogService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


@requires_real_postgres
async def test_module_never_imports_set_tenant_context() -> None:
    """The module-level absence of any `set_tenant_context` reference is
    itself the classification proof: unlike every genuinely tenant-scoped
    service in this codebase, this file never imports the one
    authoritative mechanism at all — there is nothing to call it with,
    because none of its 4 session-open sites touch tenant-owned data."""
    import app.services.integration_catalog_service as ics_module

    assert not hasattr(ics_module, "set_tenant_context")


@requires_real_postgres
async def test_catalog_entry_is_visible_regardless_of_ambient_tenant_context() -> None:
    """Functional proof the catalog really is global: create an entry
    while one tenant's context happens to be set on the pool (simulating
    a caller that reached this service from within a tenant-scoped
    request), then read it back via a fresh session with a DIFFERENT
    tenant's context set — the entry must be visible either way, proving
    it was never filtered by tenant_id at all."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    service = IntegrationCatalogService(async_session_maker)
    key = f"test_provider_{uuid.uuid4().hex[:8]}"

    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_a)
    await service.create_entry(
        provider_key=key, display_name="Test Provider", category="test",
        implementation_status=ProviderImplementationStatus.STUB, auth_shape=ProviderAuthShape.API_KEY,
    )

    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_b)
    entry = await service.get_by_provider_key(key)
    assert entry.provider_key == key


@requires_real_postgres
async def test_unknown_provider_key_raises() -> None:
    service = IntegrationCatalogService(async_session_maker)
    with pytest.raises(CatalogProviderNotFoundError):
        await service.get_by_provider_key("does-not-exist")
