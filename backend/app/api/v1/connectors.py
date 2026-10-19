"""One-click connectors (see `app/integrations/connectors.py`): list what can be connected, start a connection, and complete it.

`/callback` is deliberately UNAUTHENTICATED, same trust model as the QuickBooks and Google Calendar callbacks: the provider's redirect carries no JWT, so the signed
`state` token (tenant, user, provider, 10 minutes) is the only thing binding it to the tenant who started the flow. Responses never include a credential.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.security import TokenError, create_oauth_state_token, decode_oauth_state_token
from app.integrations.connectors import Connector, ConnectorCompleteError, all_connectors, get_connector, redirect_uri_for
from app.models.rbac import Permission
from app.services.integration_connection_service import IntegrationConnectionService

router = APIRouter(prefix="/integrations/connectors", tags=["integrations"])

_LIVE = ("CONNECTED", "UNVERIFIED")


class ConnectorFieldResponse(BaseModel):
    name: str
    label: str
    secret: bool
    help: str


class ConnectorResponse(BaseModel):
    key: str
    name: str
    category: str
    description: str
    mode: str
    built_in: bool
    platform_ready: bool
    missing_platform_settings: list[str]
    redirect_uri: str | None
    start_path: str | None  # set only for a connector that keeps its own earlier start endpoint
    owner_steps: list[str]
    fields: list[ConnectorFieldResponse]
    status: str
    last_error: str | None
    external_account_id: str | None


def _describe(c: Connector, connection) -> ConnectorResponse:
    if c.built_in:
        state, last_error, account = "CONNECTED", None, None
    elif connection is None:
        state, last_error, account = "NOT_CONNECTED", None, None
    else:
        state, last_error, account = str(connection.status), connection.last_error, connection.external_account_id
    return ConnectorResponse(
        key=c.key, name=c.name, category=c.category, description=c.description, mode=c.mode, built_in=c.built_in,
        platform_ready=c.platform_ready(), missing_platform_settings=c.missing_settings(),
        redirect_uri=redirect_uri_for(c.key) if c.mode == "oauth" else None, start_path=c.extra.get("start_path"),
        owner_steps=list(c.owner_steps), fields=[ConnectorFieldResponse(name=f.name, label=f.label, secret=f.secret, help=f.help) for f in c.fields],
        status=state, last_error=last_error, external_account_id=account,
    )


def _require(key: str) -> Connector:
    connector = get_connector(key)
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown connector {key!r}")
    return connector


def _require_ready(connector: Connector) -> str | None:
    missing = connector.missing_settings()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{connector.name} is not set up on this Klaros deployment yet. Missing platform settings: {', '.join(missing)}",
        )
    if connector.mode == "oauth":
        uri = redirect_uri_for(connector.key)
        if uri is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="KLAROS_PUBLIC_API_URL is not set, so the callback address is unknown")
        return uri
    return redirect_uri_for(connector.key)


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> list[ConnectorResponse]:
    by_provider = {c.provider: c for c in await service.list_connections(current_user.tenant_id)}
    return [_describe(c, by_provider.get(c.key)) for c in all_connectors().values()]


@router.post("/{key}/start")
async def start_connector(
    key: str,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
) -> dict[str, Any]:
    connector = _require(key)
    if connector.start is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{connector.name} connects by pasting credentials, not by a start step")
    redirect_uri = _require_ready(connector) or ""
    state = create_oauth_state_token(current_user.tenant_id, key, current_user.id)
    return await connector.start(current_user.tenant_id, current_user.id, redirect_uri, state)


@router.get("/{key}/callback")
async def connector_callback(
    key: str,
    request: Request,
    service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> RedirectResponse:
    connector = _require(key)
    settings = get_settings()
    params = dict(request.query_params)

    def back(outcome: str, detail: str | None = None) -> RedirectResponse:
        query = {"connector": key, "result": outcome}
        if detail:
            query["detail"] = detail
        return RedirectResponse(url=f"{settings.FRONTEND_BASE_URL}/settings/integrations?{urlencode(query)}", status_code=status.HTTP_302_FOUND)

    if params.get("error"):
        return back("error", params["error"])  # the owner declined consent: an expected outcome
    state = params.get("state")
    if connector.complete is None or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing state in callback")
    try:
        payload = decode_oauth_state_token(state, expected_provider=key)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid or expired state token: {exc}") from exc
    tenant_id, user_id = uuid.UUID(payload["tenant_id"]), uuid.UUID(payload["sub"])
    try:
        result = await connector.complete(params, redirect_uri_for(key) or "", tenant_id)
    except ConnectorCompleteError as exc:
        return back("error", str(exc))
    connection = await service.connect(
        tenant_id, key, result.credential, created_by=user_id, external_account_id=result.external_account_id, scopes=result.scopes,
    )
    if str(connection.status) not in _LIVE:
        return back("error", connection.last_error or "verification_failed")
    return back("connected")


class CompleteBody(BaseModel):
    params: dict[str, Any]


@router.post("/{key}/complete", response_model=ConnectorResponse)
async def complete_connector(
    key: str,
    body: CompleteBody,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> ConnectorResponse:
    """For `link` connectors: the browser finished the provider's widget and posts its result here."""
    connector = _require(key)
    if connector.complete is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{connector.name} has no completion step")
    _require_ready(connector)
    try:
        result = await connector.complete(body.params, redirect_uri_for(key) or "", current_user.tenant_id)
    except ConnectorCompleteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    connection = await service.connect(
        current_user.tenant_id, key, result.credential, created_by=current_user.id,
        external_account_id=result.external_account_id, scopes=result.scopes,
    )
    return _describe(connector, connection)

