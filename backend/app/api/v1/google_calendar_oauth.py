"""Phase 14: Google Calendar OAuth2 connect flow — mirrors
`app/api/v1/quickbooks_oauth.py` exactly (same reasoning, third OAuth
provider on the same `IntegrationConnectionService` model).

`/authorize` (authenticated — a tenant admin explicitly starting the
flow) returns the real Google consent-page URL, with a signed `state`
token binding the callback back to this tenant/user (reuses
`app/core/security.py::create_oauth_state_token` — the exact same
primitive QuickBooks uses, not a second one).

`/callback` (deliberately UNAUTHENTICATED, same trust model as
`app/api/v1/webhooks.py`/`quickbooks_oauth.py` — Google's redirect
carries no JWT) is the one place the platform app's client_secret is
used to exchange the real `code` for real tokens, then stores them via
the existing, generic `IntegrationConnectionService.connect()`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.security import TokenError, create_oauth_state_token, decode_oauth_state_token
from app.integrations.google_calendar_client import GoogleCalendarAPIError, GoogleCalendarClient, get_authorization_url
from app.models.rbac import Permission
from app.services.integration_connection_service import IntegrationConnectionService

router = APIRouter(prefix="/integrations/google-calendar", tags=["integrations"])

_PROVIDER = "google_calendar"


@router.get("/authorize")
async def google_calendar_authorize(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
) -> dict[str, str]:
    settings = get_settings()
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_REDIRECT_URI:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth app is not configured (GOOGLE_CLIENT_ID/GOOGLE_REDIRECT_URI unset)",
        )
    state = create_oauth_state_token(current_user.tenant_id, _PROVIDER, current_user.id)
    url = get_authorization_url(
        client_id=settings.GOOGLE_CLIENT_ID, redirect_uri=settings.GOOGLE_REDIRECT_URI, state=state,
    )
    return {"authorization_url": url}


@router.get("/callback")
async def google_calendar_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> RedirectResponse:
    settings = get_settings()
    failure_redirect = f"{settings.FRONTEND_BASE_URL}/settings/integrations?google_calendar=error"

    if error:
        # The tenant declined consent, or Google itself rejected the
        # request — a real, expected outcome, not a bug.
        return RedirectResponse(url=f"{failure_redirect}&detail={error}", status_code=status.HTTP_302_FOUND)

    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing code/state in Google callback")

    try:
        payload = decode_oauth_state_token(state, expected_provider=_PROVIDER)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid or expired state token: {exc}") from exc

    tenant_id = uuid.UUID(payload["tenant_id"])
    user_id = uuid.UUID(payload["sub"])

    client = GoogleCalendarClient()
    try:
        tokens = await client.exchange_code_for_tokens(code=code, redirect_uri=settings.GOOGLE_REDIRECT_URI or "")
    except GoogleCalendarAPIError:
        return RedirectResponse(url=f"{failure_redirect}&detail=token_exchange_failed", status_code=status.HTTP_302_FOUND)

    if not tokens.refresh_token:
        # Should not happen given access_type=offline&prompt=consent, but
        # a refresh_token is required for this integration to keep
        # working past the ~1h access-token lifetime — fail honestly
        # rather than store a connection that will silently stop working.
        return RedirectResponse(url=f"{failure_redirect}&detail=no_refresh_token", status_code=status.HTTP_302_FOUND)

    await service.connect(
        tenant_id, _PROVIDER,
        {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token},
        created_by=user_id, external_account_id=None, scopes=tokens.scope,
    )

    return RedirectResponse(
        url=f"{settings.FRONTEND_BASE_URL}/settings/integrations?google_calendar=connected",
        status_code=status.HTTP_302_FOUND,
    )
