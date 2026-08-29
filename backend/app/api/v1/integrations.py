import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.api.tool_deps_integrations import get_integration_connection_service
from app.integrations.adapters import ALL_ADAPTERS
from app.models.rbac import Permission
from app.services.integration_connection_service import (
    ConnectionNotFoundError,
    IntegrationConnectionService,
)

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
