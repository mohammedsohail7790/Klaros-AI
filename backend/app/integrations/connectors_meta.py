"""Meta Ads one-click connector: Facebook Login (scope ads_read) with Klaros's own Meta app. The code is exchanged for a short-lived token and
then a long-lived one (about 60 days); the credential records `expires_at` so verify reports an expired token honestly and the daily sync can extend it before it lapses."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.integrations import meta_client as mc
from app.integrations.connectors import CompleteResult, Connector, ConnectorCompleteError
from app.integrations.meta_client import MetaAPIError, MetaClient

PROVIDER = "meta_ads"


async def _start(tenant_id: uuid.UUID, user_id: uuid.UUID, redirect_uri: str, state: str) -> dict[str, Any]:
    settings = get_settings()
    app_id = settings.META_ADS_APP_ID
    if not app_id:
        raise ConnectorCompleteError("Meta Ads is not set up on this platform yet (META_ADS_APP_ID is missing).")
    return {"authorization_url": mc.get_authorization_url(app_id=app_id, redirect_uri=redirect_uri, state=state, config_id=settings.META_ADS_LOGIN_CONFIG_ID)}


async def _complete(params: dict[str, Any], redirect_uri: str, tenant_id: uuid.UUID) -> CompleteResult:
    if params.get("error"):
        raise ConnectorCompleteError(f"Meta did not grant access ({str(params.get('error_reason') or params.get('error'))[:80]}).")
    code = params.get("code")
    if not code:
        raise ConnectorCompleteError("Meta did not send an authorization code.")
    client = MetaClient()
    try:
        short = await client.exchange_code(code=str(code), redirect_uri=redirect_uri)
        long = await client.exchange_for_long_lived(access_token=short.access_token)
        accounts = await client.list_ad_accounts(access_token=long.access_token)
    except MetaAPIError as exc:
        raise ConnectorCompleteError(str(exc)) from exc
    if not accounts:
        raise ConnectorCompleteError("This Facebook login has no ad account Klaros can read.")
    chosen = next((a for a in accounts if a.account_status == 1), accounts[0])
    credential = {
        "access_token": long.access_token, "ad_account_id": chosen.id, "ad_account_name": chosen.name, "currency": chosen.currency,
        "expires_at": long.expires_at.isoformat() if long.expires_at else None,
        "accessible_ad_account_ids": [a.id for a in accounts],
    }
    return CompleteResult(credential=credential, external_account_id=chosen.id, scopes=",".join(mc.SCOPES))


async def _verify(credential: dict) -> tuple[bool, str]:
    token, account_id = credential.get("access_token"), credential.get("ad_account_id")
    if not token or not account_id:
        return False, "No access token or ad account is stored; reconnect Meta Ads."
    if mc.token_expiry_state(credential.get("expires_at")) == "expired":
        return False, "The Meta access token has expired (they last about 60 days). Reconnect Meta Ads."
    try:
        account = await MetaClient().get_ad_account(access_token=token, ad_account_id=account_id)
    except MetaAPIError as exc:
        return False, str(exc)
    state = "active" if account.account_status == 1 else f"status {account.account_status}"
    return True, f"Meta ad account '{account.name}' reachable ({state})."


CONNECTORS = [
    Connector(
        key=PROVIDER, name="Meta Ads", category="marketing", mode="oauth",
        description="Reads daily campaign spend from the business's Facebook and Instagram ad account so real ad spend shows up beside leads and revenue. Read-only.",
        required_settings=("META_ADS_APP_ID", "META_ADS_APP_SECRET"),
        start=_start, complete=_complete, verify=_verify,
        owner_steps=(
            "At developers.facebook.com create a Business-type app and add the Facebook Login product.",
            "Add the redirect URI shown here under Valid OAuth Redirect URIs; add app domain and privacy policy URL in App settings.",
            "Request Advanced access to ads_read through App Review (Business Verification may be required); until then only app roles (admins, developers, testers) can connect.",
            "In Facebook Login for Business > Configurations create a configuration (user access token, permission ads_read) and note its Configuration ID.",
            "Set META_ADS_APP_ID, META_ADS_APP_SECRET and META_ADS_LOGIN_CONFIG_ID in Render and switch the app to Live.",
            "Back in Klaros press Connect and sign in with the Facebook user that has access to the business's ad account. The connection lasts about 60 days and Klaros extends it automatically.",
        ),
    )
]
