"""Google Business Profile one-click connector: OAuth (scope business.manage, offline) with Klaros's own Google Cloud OAuth client. Read-only in this version: Klaros
reads the business's locations and reviews; it never posts a reply. The stored credential holds a refresh token (access tokens last about an hour, so every use refreshes
first) and the Business Profile account the owner's login reaches."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.integrations import google_business_client as gbc
from app.integrations.connectors import CompleteResult, Connector, ConnectorCompleteError
from app.integrations.google_business_client import GoogleBusinessAPIError, GoogleBusinessClient

PROVIDER = "google_business_profile"


async def _start(tenant_id: uuid.UUID, user_id: uuid.UUID, redirect_uri: str, state: str) -> dict[str, Any]:
    client_id = get_settings().GOOGLE_BUSINESS_CLIENT_ID
    if not client_id:
        raise ConnectorCompleteError("Google Business Profile is not set up on this platform yet (GOOGLE_BUSINESS_CLIENT_ID is missing).")
    return {"authorization_url": gbc.get_authorization_url(client_id=client_id, redirect_uri=redirect_uri, state=state)}


async def _complete(params: dict[str, Any], redirect_uri: str, tenant_id: uuid.UUID) -> CompleteResult:
    if params.get("error"):
        raise ConnectorCompleteError(f"Google did not grant access ({str(params.get('error'))[:80]}).")
    code = params.get("code")
    if not code:
        raise ConnectorCompleteError("Google did not send an authorization code.")
    client = GoogleBusinessClient()
    try:
        tokens = await client.exchange_code(code=str(code), redirect_uri=redirect_uri)
        if not tokens.refresh_token:
            raise ConnectorCompleteError(
                "Google did not return a refresh token, so Klaros could not keep the connection alive. Remove Klaros at myaccount.google.com/permissions and connect again."
            )
        accounts = await client.list_accounts(access_token=tokens.access_token)
    except GoogleBusinessAPIError as exc:
        raise ConnectorCompleteError(str(exc)) from exc
    if not accounts:
        raise ConnectorCompleteError("This Google login manages no Business Profile account. Sign in with the Google user that owns or manages the business's Business Profile.")
    names = [a.name for a in accounts]
    credential = {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token, "account_name": names[0], "accessible_accounts": names}
    return CompleteResult(credential=credential, external_account_id=names[0], scopes=gbc.SCOPE)


async def _verify(credential: dict) -> tuple[bool, str]:
    refresh = credential.get("refresh_token")
    if not refresh:
        return False, "No refresh token is stored; reconnect Google Business Profile."
    client = GoogleBusinessClient()
    try:
        tokens = await client.refresh_access_token(refresh_token=refresh)
        accounts = await client.list_accounts(access_token=tokens.access_token)
    except GoogleBusinessAPIError as exc:
        return False, str(exc)
    if not accounts:
        return False, "Connected to Google but no Business Profile account is reachable with this login."
    wanted = credential.get("account_name")
    if wanted and wanted not in {a.name for a in accounts}:
        return False, f"Business Profile account {wanted} is no longer reachable with this login."
    return True, f"Google Business Profile reachable ({len(accounts)} account(s))."


CONNECTORS = [
    Connector(
        key=PROVIDER, name="Google Business Profile", category="marketing", mode="oauth",
        description="Reads the business's Google locations and customer reviews into Klaros's reputation records and flags new low-star reviews. Read-only: Klaros never posts replies.",
        required_settings=("GOOGLE_BUSINESS_CLIENT_ID", "GOOGLE_BUSINESS_CLIENT_SECRET"),
        start=_start, complete=_complete, verify=_verify,
        owner_steps=(
            "In the Google Cloud project Klaros uses, enable three APIs: My Business Account Management API, My Business Business Information API and Google My Business API (the Business Profile APIs that serve reviews).",
            "Submit Google's Business Profile API access request form for that project's number, using a business email and a Business Profile that is verified and at least 60 days old. Review typically takes from a few days to a couple of weeks; until Google approves, every API call returns 403 or quota 0 and Klaros will say so.",
            "Configure the OAuth consent screen: add the sensitive scope https://www.googleapis.com/auth/business.manage and, while the app is in Testing, add the Google users who manage the Business Profile as test users (a published app with a sensitive scope needs Google verification).",
            "Create an OAuth client of type Web application and add the redirect URI shown here as an Authorized redirect URI.",
            "Set GOOGLE_BUSINESS_CLIENT_ID and GOOGLE_BUSINESS_CLIENT_SECRET in Render and redeploy.",
            "Back in Klaros press Connect and sign in with the Google user that owns or manages the business's Business Profile, then allow access.",
        ),
    )
]
