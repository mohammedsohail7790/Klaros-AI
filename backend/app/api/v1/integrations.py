import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.api.tool_deps_integrations import (
    get_integration_catalog_service,
    get_integration_connection_service,
)
from app.integrations.adapters import ALL_ADAPTERS
from app.models.rbac import Permission
from app.services.integration_catalog_service import (
    CatalogProviderAlreadyExistsError,
    CatalogProviderNotFoundError,
    IntegrationCatalogService,
    derive_tenant_status,
)
from app.services.integration_connection_service import (
    ConnectionNotFoundError,
    IntegrationConnectionService,
)
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationStatusResponse(BaseModel):
    provider: str
    status: str
    detail: str


@router.get("", response_model=list[IntegrationStatusResponse])
async def list_integration_status(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[IntegrationStatusResponse]:
    del current_user
    # check_status() may make a real, cheap read-only API call for
    # providers that support it (Phase 12C) — run every adapter's check
    # concurrently so one slow/unreachable provider doesn't stall the rest.
    statuses = await asyncio.gather(*(adapter_cls().check_status() for adapter_cls in ALL_ADAPTERS))
    return [
        IntegrationStatusResponse(provider=s.provider, status=s.status, detail=s.detail)
        for s in statuses
    ]


# --- Phase 12D: tenant-scoped connections (for providers where each tenant
# has their OWN external account — QuickBooks, Google Calendar, Gmail,
# Google Ads, Meta Ads — distinct from the platform-level providers above) ---


class ConnectionResponse(BaseModel):
    provider: str
    status: str
    external_account_id: str | None
    scopes: str | None
    last_verified_at: datetime | None
    last_error: str | None
    # Deliberately NEVER includes encrypted_credential or any decrypted
    # credential material — this response shape is the enforcement point
    # for "never expose access tokens/API keys after submission."

    @classmethod
    def from_model(cls, c) -> "ConnectionResponse":
        return cls(
            provider=c.provider,
            status=c.status,
            external_account_id=c.external_account_id,
            scopes=c.scopes,
            last_verified_at=c.last_verified_at,
            last_error=c.last_error,
        )


class ConnectRequest(BaseModel):
    # Generic — shape varies per provider (api_key, or client_id/client_secret/
    # access_token/refresh_token for OAuth). Redacted automatically by
    # ToolRegistry's redact_input() if ever routed through a tool; this
    # endpoint itself never logs or echoes this body back.
    credential: dict[str, str]
    external_account_id: str | None = None
    scopes: str | None = None


def _get_connection_service() -> IntegrationConnectionService:
    return get_integration_connection_service()


@router.get("/connections", response_model=list[ConnectionResponse])
async def list_connections(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    service: IntegrationConnectionService = Depends(_get_connection_service),
) -> list[ConnectionResponse]:
    connections = await service.list_connections(current_user.tenant_id)
    return [ConnectionResponse.from_model(c) for c in connections]


@router.post("/connections/{provider}/connect", response_model=ConnectionResponse)
async def connect_provider(
    provider: str,
    body: ConnectRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    service: IntegrationConnectionService = Depends(_get_connection_service),
) -> ConnectionResponse:
    connection = await service.connect(
        current_user.tenant_id,
        provider,
        body.credential,
        created_by=current_user.id,
        external_account_id=body.external_account_id,
        scopes=body.scopes,
    )
    return ConnectionResponse.from_model(connection)


@router.post("/connections/{provider}/verify", response_model=ConnectionResponse)
async def verify_connection(
    provider: str,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    service: IntegrationConnectionService = Depends(_get_connection_service),
) -> ConnectionResponse:
    try:
        connection = await service.verify(current_user.tenant_id, provider)
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConnectionResponse.from_model(connection)


@router.post("/connections/{provider}/disconnect", response_model=ConnectionResponse)
async def disconnect_provider(
    provider: str,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    service: IntegrationConnectionService = Depends(_get_connection_service),
) -> ConnectionResponse:
    try:
        connection = await service.disconnect(current_user.tenant_id, provider)
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConnectionResponse.from_model(connection)


class QuickBooksImportRequest(BaseModel):
    max_records: int = 300


@router.post("/quickbooks/import")
async def import_from_quickbooks(
    body: QuickBooksImportRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.import_from_quickbooks", {"max_records": body.max_records}, execution_context(current_user)
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


# --- Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2): the
# IntegrationProviderCatalog sub-route. Deliberately nested under the
# existing `/integrations` router rather than a new top-level router — see
# KLAROS_FINAL_API_ARCHITECTURE.md's "Duplication check": this keeps the
# tenant-connection and catalog-reference concerns visibly related without
# merging their permission models. `IntegrationProviderCatalog` is
# tenant-independent reference data; only the merge with the requesting
# tenant's own `IntegrationConnection` state (below) is tenant-scoped. ---


class CatalogEntryResponse(BaseModel):
    provider_key: str
    display_name: str
    category: str
    implementation_status: str
    auth_shape: str
    description: str | None
    recommended_for_verticals: list[str]
    health_check_strategy_ref: str | None
    # Derived, per-request field — never persisted on the catalog row
    # itself. See app.services.integration_catalog_service.derive_tenant_status:
    # a STUB/WEBHOOK_NORMALIZER provider can never render CONNECTED here,
    # regardless of what any IntegrationConnection row says.
    tenant_status: str

    @classmethod
    def from_model(cls, entry, *, connection_status: str | None) -> "CatalogEntryResponse":
        return cls(
            provider_key=entry.provider_key,
            display_name=entry.display_name,
            category=entry.category,
            implementation_status=entry.implementation_status,
            auth_shape=entry.auth_shape,
            description=entry.description,
            recommended_for_verticals=list(entry.recommended_for_verticals or []),
            health_check_strategy_ref=entry.health_check_strategy_ref,
            tenant_status=derive_tenant_status(entry.implementation_status, connection_status),
        )


@router.get("/catalog", response_model=list[CatalogEntryResponse])
async def list_integration_catalog(
    category: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    catalog_service: IntegrationCatalogService = Depends(get_integration_catalog_service),
    connection_service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> list[CatalogEntryResponse]:
    """Read merges the tenant-independent catalog with the CALLING
    tenant's own connection state only — never another tenant's. This is
    the property tests/test_integration_provider_catalog.py proves
    explicitly (a catalog read must never expose or depend on any specific
    OTHER tenant's IntegrationConnection row)."""
    entries = await catalog_service.list_catalog(category=category)
    connections = await connection_service.list_connections(current_user.tenant_id)
    connection_by_provider = {c.provider: c.status for c in connections}
    return [
        CatalogEntryResponse.from_model(e, connection_status=connection_by_provider.get(e.provider_key))
        for e in entries
    ]


@router.get("/catalog/{provider_key}", response_model=CatalogEntryResponse)
async def get_integration_catalog_entry(
    provider_key: str,
    current_user: CurrentUser = Depends(get_current_user),
    catalog_service: IntegrationCatalogService = Depends(get_integration_catalog_service),
    connection_service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> CatalogEntryResponse:
    try:
        entry = await catalog_service.get_by_provider_key(provider_key)
    except CatalogProviderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    connection = await connection_service.get_connection(current_user.tenant_id, provider_key)
    return CatalogEntryResponse.from_model(
        entry, connection_status=connection.status if connection is not None else None
    )


class CatalogEntryWriteRequest(BaseModel):
    display_name: str
    category: str
    implementation_status: str
    auth_shape: str
    description: str | None = None
    recommended_for_verticals: list[str] = []
    health_check_strategy_ref: str | None = None


@router.put("/catalog/{provider_key}", response_model=CatalogEntryResponse)
async def upsert_integration_catalog_entry(
    provider_key: str,
    body: CatalogEntryWriteRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS_CATALOG)),
    catalog_service: IntegrationCatalogService = Depends(get_integration_catalog_service),
) -> CatalogEntryResponse:
    """Platform-admin catalog curation. NOTE (known limitation, recorded in
    PHASE_1_IMPLEMENTATION_LOG.md): this codebase has no cross-tenant
    "platform admin" role distinct from a tenant's own OWNER/ADMIN — the
    `MANAGE_INTEGRATIONS_CATALOG` permission is granted to OWNER/ADMIN
    within the existing per-organization RBAC model (see
    app/models/rbac.py), the closest existing approximation. A genuine
    platform-wide admin boundary, separate from any single tenant's role
    grants, is out of Phase 1's scope and deferred."""
    try:
        entry = await catalog_service.update_entry(
            provider_key,
            display_name=body.display_name,
            category=body.category,
            implementation_status=body.implementation_status,
            auth_shape=body.auth_shape,
            description=body.description,
            recommended_for_verticals=body.recommended_for_verticals,
            health_check_strategy_ref=body.health_check_strategy_ref,
        )
    except CatalogProviderNotFoundError:
        try:
            entry = await catalog_service.create_entry(
                provider_key=provider_key,
                display_name=body.display_name,
                category=body.category,
                implementation_status=body.implementation_status,
                auth_shape=body.auth_shape,
                description=body.description,
                recommended_for_verticals=body.recommended_for_verticals,
                health_check_strategy_ref=body.health_check_strategy_ref,
            )
        except CatalogProviderAlreadyExistsError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    del current_user
    return CatalogEntryResponse.from_model(entry, connection_status=None)
