"""Google Ads one-click connector: OAuth (scope adwords, offline) with Klaros's own developer app and developer token. The stored credential holds a refresh token
(access tokens last about an hour, so every use refreshes first) and the Google Ads customer the owner's account reaches."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.integrations import google_ads_client as gac
from app.integrations.connectors import CompleteResult, Connector, ConnectorCompleteError
from app.integrations.google_ads_client import GoogleAdsAPIError, GoogleAdsClient

PROVIDER = "google_ads"


async def _start(tenant_id: uuid.UUID, user_id: uuid.UUID, redirect_uri: str, state: str) -> dict[str, Any]:
    client_id = get_settings().GOOGLE_ADS_CLIENT_ID
    if not client_id:
        raise ConnectorCompleteError("Google Ads is not set up on this platform yet (GOOGLE_ADS_CLIENT_ID is missing).")
    return {"authorization_url": gac.get_authorization_url(client_id=client_id, redirect_uri=redirect_uri, state=state)}


async def _complete(params: dict[str, Any], redirect_uri: str, tenant_id: uuid.UUID) -> CompleteResult:
    if params.get("error"):
        raise ConnectorCompleteError(f"Google did not grant access ({str(params.get('error'))[:80]}).")
    code = params.get("code")
    if not code:
        raise ConnectorCompleteError("Google did not send an authorization code.")
    client = GoogleAdsClient()
    try:
        tokens = await client.exchange_code(code=str(code), redirect_uri=redirect_uri)
        if not tokens.refresh_token:
            raise ConnectorCompleteError(
                "Google did not return a refresh token, so Klaros could not keep the connection alive. Remove Klaros at myaccount.google.com/permissions and connect again."
            )
        customers = await client.list_accessible_customers(access_token=tokens.access_token)
    except GoogleAdsAPIError as exc:
        raise ConnectorCompleteError(str(exc)) from exc
    if not customers:
        raise ConnectorCompleteError("This Google account has no Google Ads account Klaros can reach.")
    customer_id = customers[0]
    credential = {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token, "customer_id": customer_id, "accessible_customer_ids": customers}
    return CompleteResult(credential=credential, external_account_id=customer_id, scopes=gac.SCOPE)


async def _verify(credential: dict) -> tuple[bool, str]:
    refresh = credential.get("refresh_token")
    if not refresh:
        return False, "No refresh token is stored; reconnect Google Ads."
    client = GoogleAdsClient()
    try:
        tokens = await client.refresh_access_token(refresh_token=refresh)
        customers = await client.list_accessible_customers(access_token=tokens.access_token)
    except GoogleAdsAPIError as exc:
        return False, str(exc)
    wanted = credential.get("customer_id")
    if not customers:
        return False, "Connected to Google but no Google Ads account is reachable."
    if wanted and wanted not in customers:
        return False, f"Google Ads account {wanted} is no longer reachable with this login."
    return True, f"Google Ads reachable ({len(customers)} account(s))."


CONNECTORS = [
    Connector(
        key=PROVIDER, name="Google Ads", category="marketing", mode="oauth",
        description="Reads daily campaign cost from the business's Google Ads account so real ad spend shows up beside leads and revenue. Read-only.",
        required_settings=("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_DEVELOPER_TOKEN"),
        start=_start, complete=_complete, verify=_verify,
        owner_steps=(
            "In Google Cloud create a project, enable the Google Ads API, and create an OAuth client (Web application).",
            "Add the redirect URI shown here as an Authorized redirect URI; configure the OAuth consent screen (scope adwords) and add test users while it is in Testing.",
            "In a Google Ads manager account open API Center and apply for a developer token (Basic access); until approved it only works on test accounts.",
            "Set GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET and GOOGLE_ADS_DEVELOPER_TOKEN in Render.",
            "Back in Klaros press Connect and sign in with the Google user that has access to the business's Ads account.",
        ),
    )
]
