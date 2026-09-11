"""Phase 13: QuickBooks Online OAuth2 connect flow.

Distinct from the generic `/integrations/connections/{provider}/connect`
endpoint (`app/api/v1/integrations.py`) — that endpoint is for providers
where the CALLER already has a finished credential in hand (an API key,
like Stripe's `sk_...`). QuickBooks uses a real OAuth2 authorization-code
grant instead: the tenant's browser must visit Intuit's own consent page,
and Intuit redirects back to US with a `code` — there is no credential a
frontend form could ever collect directly (and it must not: exchanging
that code requires the platform app's `client_secret`, which must never
reach the browser).

`/authorize` (authenticated — a tenant admin explicitly starting the
flow) returns the real Intuit consent-page URL, with a signed `state`
token binding the callback back to this tenant/user (see
`app/core/security.py::create_oauth_state_token`).

`/callback` (deliberately UNAUTHENTICATED, same trust model as
`app/api/v1/webhooks.py` — Intuit's redirect carries no JWT) is the one
place the platform app's client_secret is used to exchange the real
`code` for real tokens, then stores them via the existing, generic
`IntegrationConnectionService.connect()` — no parallel credential-storage
path.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.security import TokenError, create_oauth_state_token, decode_oauth_state_token
from app.integrations.quickbooks_client import QuickBooksAPIError, QuickBooksClient, get_authorization_url
from app.models.rbac import Permission
from app.services.integration_connection_service import IntegrationConnectionService

router = APIRouter(prefix="/integrations/quickbooks", tags=["integrations"])

_PROVIDER = "quickbooks"


@router.get("/authorize")
async def quickbooks_authorize(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
) -> dict[str, str]:
    settings = get_settings()
    if not settings.QUICKBOOKS_CLIENT_ID or not settings.QUICKBOOKS_REDIRECT_URI:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QuickBooks OAuth app is not configured (QUICKBOOKS_CLIENT_ID/QUICKBOOKS_REDIRECT_URI unset)",
        )
    state = create_oauth_state_token(current_user.tenant_id, _PROVIDER, current_user.id)
    url = get_authorization_url(
        client_id=settings.QUICKBOOKS_CLIENT_ID, redirect_uri=settings.QUICKBOOKS_REDIRECT_URI, state=state,
    )
    return {"authorization_url": url}


@router.get("/callback")
async def quickbooks_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    realmId: str | None = Query(default=None),
    error: str | None = Query(default=None),
    service: IntegrationConnectionService = Depends(get_integration_connection_service),
) -> RedirectResponse:
    settings = get_settings()
    failure_redirect = f"{settings.FRONTEND_BASE_URL}/settings/integrations?quickbooks=error"

    if error:
        # The tenant declined consent, or Intuit itself rejected the
        # request — a real, expected outcome, not a bug. Redirect back
        # with an honest error marker rather than a raw 400 page.
        return RedirectResponse(url=f"{failure_redirect}&detail={error}", status_code=status.HTTP_302_FOUND)

    if not code or not state or not realmId:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing code/state/realmId in QuickBooks callback")

    try:
        payload = decode_oauth_state_token(state, expected_provider=_PROVIDER)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid or expired state token: {exc}") from exc

    tenant_id = uuid.UUID(payload["tenant_id"])
    user_id = uuid.UUID(payload["sub"])

    client = QuickBooksClient()
    try:
        tokens = await client.exchange_code_for_tokens(code=code, redirect_uri=settings.QUICKBOOKS_REDIRECT_URI or "")
    except QuickBooksAPIError:
        return RedirectResponse(url=f"{failure_redirect}&detail=token_exchange_failed", status_code=status.HTTP_302_FOUND)

    await service.connect(
        tenant_id, _PROVIDER,
        {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token, "realm_id": realmId},
        created_by=user_id, external_account_id=realmId, scopes="com.intuit.quickbooks.accounting",
    )

    return RedirectResponse(
        url=f"{settings.FRONTEND_BASE_URL}/settings/integrations?quickbooks=connected", status_code=status.HTTP_302_FOUND
    )
