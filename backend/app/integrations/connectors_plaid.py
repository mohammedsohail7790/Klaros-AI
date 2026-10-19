"""Bank feed through Plaid: the owner picks their bank inside Plaid Link; Klaros keeps only the resulting `access_token` (encrypted) and never shows it again.

Flow: `POST /integrations/connectors/plaid/start` -> `{"link_token"}` -> browser opens Plaid Link -> on success the browser posts `{"params": {"public_token", "metadata"}}` to
`/integrations/connectors/plaid/complete` -> `complete()` exchanges it and the generic connect step stores `{access_token, item_id}` and verifies it with a real call.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.integrations.connectors import CompleteResult, Connector, ConnectorCompleteError
from app.integrations.plaid_client import PlaidAPIError, PlaidClient, PlaidErrorType


async def _start(tenant_id: uuid.UUID, user_id: uuid.UUID, redirect_uri: str, state: str) -> dict[str, Any]:
    try:
        body = await PlaidClient().create_link_token(client_user_id=f"klaros-{tenant_id.hex}-{user_id.hex}")
    except PlaidAPIError as exc:
        raise ConnectorCompleteError(f"Plaid could not start the bank connection: {exc}") from exc
    token = body.get("link_token")
    if not token:
        raise ConnectorCompleteError("Plaid did not return a link token")
    return {"link_token": token, "expiration": body.get("expiration")}


async def _complete(params: dict[str, Any], redirect_uri: str, tenant_id: uuid.UUID) -> CompleteResult:
    public_token = params.get("public_token")
    if not isinstance(public_token, str) or not public_token.strip() or len(public_token) > 300:
        raise ConnectorCompleteError("Plaid Link did not return a public token")
    try:
        body = await PlaidClient().exchange_public_token(public_token.strip())
    except PlaidAPIError as exc:
        raise ConnectorCompleteError(f"Plaid refused the bank connection: {exc}") from exc
    access_token, item_id = body.get("access_token"), body.get("item_id")
    if not access_token or not item_id:
        raise ConnectorCompleteError("Plaid did not return a bank connection")
    return CompleteResult(credential={"access_token": access_token, "item_id": item_id}, external_account_id=item_id, scopes="transactions")


async def _verify(credential: dict) -> tuple[bool, str]:
    """A real read-only call (/item/get). Plaid reports a broken bank login inside a normal 200 body (`item.error`), and sometimes as a 400; both are reported honestly."""
    access_token = credential.get("access_token")
    if not access_token:
        return False, "No Plaid access token is stored"
    try:
        body = await PlaidClient().get_item(access_token)
    except PlaidAPIError as exc:
        if exc.error_code == "ITEM_LOGIN_REQUIRED":
            return False, "ITEM_LOGIN_REQUIRED: the bank needs the owner to sign in again"
        if exc.error_type is PlaidErrorType.NOT_CONFIGURED:
            return False, str(exc)
        return False, f"Plaid rejected the connection: {exc}"
    item = body.get("item") or {}
    error = item.get("error")
    if error:
        code = error.get("error_code") or "ITEM_ERROR"
        if code == "ITEM_LOGIN_REQUIRED":
            return False, "ITEM_LOGIN_REQUIRED: the bank needs the owner to sign in again"
        return False, f"{code}: {error.get('display_message') or error.get('error_message') or 'the bank connection has a problem'}"
    return True, f"verified via POST /item/get (institution {item.get('institution_id') or 'unknown'})"


CONNECTORS = [
    Connector(
        key="plaid", name="Bank feed (Plaid)", category="finance", mode="link",
        description="Connects the business's bank account read-only, pulls transactions every day and suggests which deposits are payments for open invoices.",
        required_settings=("PLAID_CLIENT_ID", "PLAID_SECRET"),
        owner_steps=(
            "Create a free developer account at dashboard.plaid.com and complete the company profile and the use-case questionnaire.",
            "Copy the client_id and the Sandbox secret from Developers > Keys into Render as PLAID_CLIENT_ID and PLAID_SECRET (PLAID_ENV stays sandbox for testing).",
            "In the Plaid dashboard request Production access (Transactions product); Plaid reviews it, then give the Production secret to Render as PLAID_SECRET and set PLAID_ENV to production.",
            "Back in Klaros press Connect, pick the bank in the Plaid window and sign in to it. Klaros only reads; it cannot move money.",
        ),
        start=_start, complete=_complete, verify=_verify,
        extra={"countries": ["US", "CA", "GB"]},
    )
]
