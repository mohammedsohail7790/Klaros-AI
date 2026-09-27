"""Phase 1.2 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2,
KLAROS_FINAL_INTEGRATION_MODEL.md): `IntegrationProviderCatalog`.

Covers: creation, uniqueness, retrieval, status-derivation (the
implementation_status/tenant_status two-axis rule), invalid-data
handling, seed-data-matches-verified-reality, authorization
(unauthenticated / wrong role / OWNER), and the "a catalog read must never
depend on another tenant's IntegrationConnection" property.
"""

import uuid

import pytest

from app.data.integration_provider_catalog_seed import (
    EXPECTED_REAL,
    EXPECTED_STUB,
    EXPECTED_WEBHOOK_NORMALIZER,
    SEED_PROVIDERS,
)
from app.models.integration_catalog import ProviderImplementationStatus
from app.services.integration_catalog_service import (
    CatalogProviderAlreadyExistsError,
    CatalogProviderNotFoundError,
    IntegrationCatalogService,
    derive_tenant_status,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tool_registry) -> IntegrationCatalogService:
    from app.db.session import async_session_maker

    return IntegrationCatalogService(async_session_maker)


async def _seed_catalog(service: IntegrationCatalogService) -> None:
    for p in SEED_PROVIDERS:
        await service.create_entry(
            provider_key=p["provider_key"],
            display_name=p["display_name"],
            category=p["category"],
            implementation_status=str(p["implementation_status"]),
            auth_shape=str(p["auth_shape"]),
            description=p["description"],
            recommended_for_verticals=p["recommended_for_verticals"],
            health_check_strategy_ref=p["health_check_strategy_ref"],
        )


async def test_create_and_get_entry(service: IntegrationCatalogService) -> None:
    entry = await service.create_entry(
        provider_key="testprovider", display_name="Test Provider", category="finance",
        implementation_status=ProviderImplementationStatus.STUB, auth_shape="API_KEY",
    )
    fetched = await service.get_by_provider_key("testprovider")
    assert fetched.id == entry.id
    assert fetched.implementation_status == ProviderImplementationStatus.STUB


async def test_provider_key_uniqueness_enforced(service: IntegrationCatalogService) -> None:
    await service.create_entry(
        provider_key="dupprovider", display_name="Dup", category="finance",
        implementation_status="REAL", auth_shape="API_KEY",
    )
    with pytest.raises(CatalogProviderAlreadyExistsError):
        await service.create_entry(
            provider_key="dupprovider", display_name="Dup 2", category="finance",
            implementation_status="REAL", auth_shape="API_KEY",
        )


async def test_get_unknown_provider_raises_not_found(service: IntegrationCatalogService) -> None:
    with pytest.raises(CatalogProviderNotFoundError):
        await service.get_by_provider_key("no-such-provider")


async def test_update_unknown_provider_raises_not_found(service: IntegrationCatalogService) -> None:
    with pytest.raises(CatalogProviderNotFoundError):
        await service.update_entry("no-such-provider", display_name="X")


async def test_seed_data_matches_verified_reality(service: IntegrationCatalogService) -> None:
    """KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2's named regression guard:
    if a stub provider's adapter is later actually implemented, this test
    must be updated deliberately (by editing
    app/data/integration_provider_catalog_seed.py), never silently drift."""
    await _seed_catalog(service)
    entries = await service.list_catalog()
    by_status: dict[str, set[str]] = {}
    for e in entries:
        by_status.setdefault(e.implementation_status, set()).add(e.provider_key)

    assert by_status.get(ProviderImplementationStatus.REAL, set()) == EXPECTED_REAL
    assert by_status.get(ProviderImplementationStatus.STUB, set()) == EXPECTED_STUB
    assert by_status.get(ProviderImplementationStatus.WEBHOOK_NORMALIZER, set()) == EXPECTED_WEBHOOK_NORMALIZER


async def test_list_filters_by_category(service: IntegrationCatalogService) -> None:
    await _seed_catalog(service)
    finance_entries = await service.list_catalog(category="finance")
    assert {e.provider_key for e in finance_entries} == {"stripe", "quickbooks", "xero"}


# --- Status-derivation rule (KLAROS_FINAL_INTEGRATION_MODEL.md's hard
# constraint): rendered status = min(implementation_status ceiling,
# tenant connection status). A STUB/WEBHOOK_NORMALIZER provider must NEVER
# render CONNECTED, no matter what a tenant's IntegrationConnection says. ---


# Pure-function unit tests for a synchronous helper. `pytestmark` above is
# module-scoped (`pytest.mark.asyncio`) for the async DB/HTTP tests in this
# file; these three are trivially `async def` (no real await needed) purely
# so they don't trip pytest-asyncio's "marked async but not an async
# function" warning under `asyncio_mode=auto`.


async def test_derive_tenant_status_stub_never_renders_connected() -> None:
    assert derive_tenant_status(ProviderImplementationStatus.STUB, "CONNECTED") == "STUB"
    assert derive_tenant_status(ProviderImplementationStatus.STUB, None) == "STUB"


async def test_derive_tenant_status_webhook_normalizer_never_renders_connected() -> None:
    assert derive_tenant_status(ProviderImplementationStatus.WEBHOOK_NORMALIZER, "CONNECTED") == "WEBHOOK_NORMALIZER"


async def test_derive_tenant_status_real_reflects_tenant_connection() -> None:
    assert derive_tenant_status(ProviderImplementationStatus.REAL, "CONNECTED") == "CONNECTED"
    assert derive_tenant_status(ProviderImplementationStatus.REAL, "ERROR") == "ERROR"
    assert derive_tenant_status(ProviderImplementationStatus.REAL, None) == "NOT_CONNECTED"


# --- HTTP surface: authorization + tenant-independence -------------------


async def _register(client, org_name: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org_name, "full_name": "Owner Test",
            "email": email, "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def test_catalog_read_requires_authentication(client) -> None:
    resp = await client.get("/api/v1/integrations/catalog")
    assert resp.status_code in (401, 403)


async def test_catalog_read_over_http_returns_seeded_entries(client, tool_registry) -> None:
    from app.db.session import async_session_maker

    await _seed_catalog(IntegrationCatalogService(async_session_maker))
    token = await _register(client, "Catalog Read Co", "owner@catalogreadco.com")
    resp = await client.get("/api/v1/integrations/catalog", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {e["provider_key"] for e in body} == EXPECTED_REAL | EXPECTED_STUB | EXPECTED_WEBHOOK_NORMALIZER
    stripe = next(e for e in body if e["provider_key"] == "stripe")
    assert stripe["tenant_status"] == "NOT_CONNECTED"  # REAL, but this tenant has no connection yet
    xero = next(e for e in body if e["provider_key"] == "xero")
    assert xero["tenant_status"] == "STUB"


async def test_catalog_write_requires_manage_integrations_catalog_permission(client) -> None:
    """A non-OWNER/ADMIN role (e.g. TECHNICIAN, granted no
    MANAGE_INTEGRATIONS_CATALOG) must be rejected."""
    from sqlalchemy import select

    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    owner_token = await _register(client, "Catalog Write Co", "owner@catalogwriteco.com")
    del owner_token

    async with async_session_maker() as session:
        owner = (await session.execute(select(User).where(User.email == "owner@catalogwriteco.com"))).scalar_one()
        tenant_id = owner.tenant_id
        tech = User(
            tenant_id=tenant_id, email="tech@catalogwriteco.com", full_name="Tech User",
            hashed_password=hash_password("supersecret1"), role=Role.TECHNICIAN,
        )
        session.add(tech)
        await session.commit()
        await session.refresh(tech)
        tech_token = create_access_token(tech.id, tenant_id, Role.TECHNICIAN)

    resp = await client.put(
        "/api/v1/integrations/catalog/stripe",
        json={
            "display_name": "Stripe", "category": "finance",
            "implementation_status": "REAL", "auth_shape": "API_KEY",
        },
        headers={"Authorization": f"Bearer {tech_token}"},
    )
    assert resp.status_code == 403


async def test_catalog_write_allowed_for_owner_and_is_platform_wide(client) -> None:
    owner_token = await _register(client, "Catalog Owner Co", "owner@catalogownerco.com")
    resp = await client.put(
        "/api/v1/integrations/catalog/new_provider",
        json={
            "display_name": "New Provider", "category": "finance",
            "implementation_status": "STUB", "auth_shape": "API_KEY",
            "description": "created via test",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider_key"] == "new_provider"

    # A second, unrelated tenant reads the SAME global catalog row — proves
    # this is platform-wide reference data, not tenant-scoped.
    other_owner_token = await _register(client, "Other Tenant Co", "owner@othertenantco.com")
    read_resp = await client.get(
        "/api/v1/integrations/catalog/new_provider", headers={"Authorization": f"Bearer {other_owner_token}"}
    )
    assert read_resp.status_code == 200
    assert read_resp.json()["display_name"] == "New Provider"


async def test_catalog_read_never_depends_on_another_tenants_connection(client) -> None:
    """The property KLAROS_FINAL_INTEGRATION_MODEL.md requires: tenant A's
    catalog read must never expose or be influenced by tenant B's
    IntegrationConnection state."""
    from app.db.session import async_session_maker

    await _seed_catalog(IntegrationCatalogService(async_session_maker))

    tenant_a_token = await _register(client, "Tenant A Connect Co", "owner@tenantaconnectco.com")
    connect_resp = await client.post(
        "/api/v1/integrations/connections/stripe/connect",
        json={"credential": {"secret_key": "sk_test_not_real"}},
        headers={"Authorization": f"Bearer {tenant_a_token}"},
    )
    # Connecting may succeed (stored, unverified) or fail verification —
    # either way, what matters is tenant B never sees it.
    del connect_resp

    tenant_b_token = await _register(client, "Tenant B Reads Co", "owner@tenantbreadsco.com")
    catalog_resp = await client.get(
        "/api/v1/integrations/catalog", headers={"Authorization": f"Bearer {tenant_b_token}"}
    )
    assert catalog_resp.status_code == 200
    stripe_row = next(e for e in catalog_resp.json() if e["provider_key"] == "stripe")
    assert stripe_row["tenant_status"] == "NOT_CONNECTED"
