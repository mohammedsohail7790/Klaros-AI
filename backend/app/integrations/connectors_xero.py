"""Xero one-click connector: OAuth 2.0 authorization code flow with Klaros's own Xero app (read-only scopes). The stored credential holds the access token, the ROTATING
refresh token (every refresh returns a new one; see xero_client), its expiry, and the Xero organisation (xero tenant) the owner's login reaches."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.integrations import xero_client as xc
from app.integrations.connectors import CompleteResult, Connector, ConnectorCompleteError
from app.integrations.xero_client import XeroAPIError, XeroClient, XeroErrorType

PROVIDER = "xero"


async def _start(tenant_id: uuid.UUID, user_id: uuid.UUID, redirect_uri: str, state: str) -> dict[str, Any]:
    client_id = get_settings().XERO_CLIENT_ID
    if not client_id:
        raise ConnectorCompleteError("Xero is not set up on this platform yet (XERO_CLIENT_ID is missing).")
    return {"authorization_url": xc.get_authorization_url(client_id=client_id, redirect_uri=redirect_uri, state=state)}


async def _complete(params: dict[str, Any], redirect_uri: str, tenant_id: uuid.UUID) -> CompleteResult:
    if params.get("error"):
        raise ConnectorCompleteError(f"Xero did not grant access ({str(params.get('error'))[:80]}).")
    code = params.get("code")
    if not code:
        raise ConnectorCompleteError("Xero did not send an authorization code.")
    client = XeroClient()
    try:
        tokens = await client.exchange_code(code=str(code), redirect_uri=redirect_uri)
        connections = await client.list_connections(access_token=tokens.access_token)
    except XeroAPIError as exc:
        raise ConnectorCompleteError(str(exc)) from exc
    if not connections:
        raise ConnectorCompleteError("This Xero login has no organisation connected to Klaros. Connect again and tick an organisation on the Xero consent screen.")
    chosen = connections[0]
    credential = {
        **tokens.to_credential_update(),
        "xero_tenant_id": chosen.tenant_id,
        "tenant_name": chosen.tenant_name,
        "accessible_tenants": [{"id": c.tenant_id, "name": c.tenant_name} for c in connections],
    }
    return CompleteResult(credential=credential, external_account_id=chosen.tenant_id, scopes=xc.SCOPES)


async def _verify(credential: dict) -> tuple[bool, str]:
    """Real Organisation call. Refreshes first when the access token is near expiry; the rotated tokens are merged into `credential` IN PLACE and
    IntegrationConnectionService.verify() persists them (never lose the new refresh token)."""
    xero_tenant_id = credential.get("xero_tenant_id")
    if not xero_tenant_id:
        return False, "No Xero organisation is stored; reconnect Xero."
    client = XeroClient()
    try:
        await xc.ensure_fresh_credential(client, credential)
        try:
            org = await client.get_organisation(access_token=credential["access_token"], xero_tenant_id=xero_tenant_id)
        except XeroAPIError as exc:
            if exc.error_type != XeroErrorType.AUTHENTICATION or exc.status_code == 403:
                raise
            await xc.ensure_fresh_credential(client, credential, force=True)  # the access token was rejected early: try once with a fresh one
            org = await client.get_organisation(access_token=credential["access_token"], xero_tenant_id=xero_tenant_id)
    except XeroAPIError as exc:
        if exc.error_type == XeroErrorType.REFRESH_REVOKED:
            return False, "The Xero login expired or was revoked; reconnect Xero."
        if exc.status_code == 403:
            return False, "Xero refused access to this organisation (it may have been disconnected); reconnect Xero."
        return False, str(exc)
    return True, f"Xero reachable (organisation: {org.get('Name') or credential.get('tenant_name') or xero_tenant_id})."


CONNECTORS = [
    Connector(
        key=PROVIDER, name="Xero", category="finance", mode="oauth",
        description="Connects the business's Xero organisation (read-only) and imports its contacts as Klaros customers without duplicates.",
        required_settings=("XERO_CLIENT_ID", "XERO_CLIENT_SECRET"),
        start=_start, complete=_complete, verify=_verify,
        owner_steps=(
            "Sign in at developer.xero.com, open My Apps and choose New app. Name it, choose the app type Web app, enter your company URL, and accept the terms.",
            "Under Configuration add the redirect URI shown here exactly (it ends in /api/v1/integrations/connectors/xero/callback) and save.",
            "Open Configuration, copy the Client id, then generate a Client secret (shown once; copy it immediately).",
            "Set XERO_CLIENT_ID and XERO_CLIENT_SECRET in Render and redeploy. Klaros asks only for read-only scopes (contacts, transactions, settings).",
            "Test with Xero's Demo Company (in Xero choose your organisation name, then Try the demo company), then connect a real organisation.",
            "Know the limits: an uncertified app can connect at most 25 organisations in total. Beyond that, complete Xero's app certification (Partner Program) before onboarding more tenants.",
            "Back in Klaros press Connect, sign in to Xero and tick the organisation to share. If several are ticked Klaros uses the first and records the rest.",
        ),
    )
]
