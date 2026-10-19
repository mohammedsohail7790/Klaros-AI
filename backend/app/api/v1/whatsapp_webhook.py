"""WhatsApp (Meta Cloud API): the public webhook Meta calls, and the owner's page of values to paste into Meta.

  GET  /webhooks/whatsapp/{tenant_id}/{key}   Meta's one-time verification (`hub.challenge`), answered only when `hub.verify_token` is this business's derived token.
  POST /webhooks/whatsapp/{tenant_id}/{key}   Meta's messages and delivery receipts. A wrong key answers 404 as if the address did not exist. When the business stored its Meta app secret, an
                                              unsigned or wrongly signed call is refused (403) and nothing is stored. Otherwise the secret address is the only protection.
  GET  /integrations/whatsapp/webhook-info    (owner/admin) the callback URL and verify token to paste into Meta. Both are derived per business (see `whatsapp_service`), never a platform secret.
  GET  /inbox/whatsapp                        (signed in) the latest inbound WhatsApp messages.
No response or log line ever contains an access token or app secret.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.core.config import get_settings
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker
from app.integrations import whatsapp_client
from app.models.rbac import Permission
from app.services import whatsapp_service

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["whatsapp"])
MAX_BODY_BYTES = 1024 * 1024
_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.get("/webhooks/whatsapp/{tenant_id}/{key}", response_class=PlainTextResponse)
async def whatsapp_verify(
    tenant_id: uuid.UUID, key: str,
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> str:
    if not whatsapp_service.url_key_ok(tenant_id, key):
        raise _NOT_FOUND
    if hub_mode != "subscribe" or not whatsapp_service.verify_token_ok(tenant_id, hub_verify_token) or hub_challenge is None:
        logger.warning("whatsapp_verify_rejected", tenant_id=str(tenant_id))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token mismatch")
    return hub_challenge[:200]


@router.post(
    "/webhooks/whatsapp/{tenant_id}/{key}",
    dependencies=[Depends(rate_limit("whatsapp_inbound", limit_setting="RATE_LIMIT_INBOUND_EMAIL_PER_MINUTE", window_seconds=60, key_func=tenant_and_ip_key))],
)
async def whatsapp_inbound(tenant_id: uuid.UUID, key: str, request: Request) -> dict[str, Any]:
    if not whatsapp_service.url_key_ok(tenant_id, key):
        raise _NOT_FOUND
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Too large")
    creds = await whatsapp_service.get_credentials(async_session_maker, tenant_id)
    if creds is None:
        raise _NOT_FOUND  # WhatsApp is not connected for this business
    if creds["app_secret"] and not whatsapp_client.verify_signature(raw, request.headers.get("x-hub-signature-256"), creds["app_secret"]):
        logger.warning("whatsapp_signature_rejected", tenant_id=str(tenant_id))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Not JSON") from None
    return await whatsapp_service.handle_payload(async_session_maker, tenant_id, payload, creds)


def _public_base(request: Request) -> str:
    configured = (get_settings().KLAROS_PUBLIC_API_URL or "").strip().rstrip("/")
    if configured:
        return configured
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base and "testserver" not in base:
        base = "https://" + base[len("http://"):]
    return base


@router.get("/integrations/whatsapp/webhook-info")
async def webhook_info(request: Request, current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS))) -> dict[str, Any]:
    """The two values the owner pastes into Meta (Callback URL and Verify token), and whether webhook calls are signature-checked."""
    tid = current_user.tenant_id
    signed = await whatsapp_service.has_app_secret(async_session_maker, tid)
    return {
        "callback_url": whatsapp_service.callback_url(_public_base(request), tid),
        "verify_token": whatsapp_service.verify_token(tid),
        "subscribe_to": ["messages"],
        "signature_check": signed,
        "note": None if signed else "Add the Meta app secret to the WhatsApp connection so Klaros can reject calls Meta did not sign. Until then the secret callback address is the only protection.",
    }


@router.get("/inbox/whatsapp")
async def list_whatsapp(limit: int = 50, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"messages": await whatsapp_service.list_inbound(async_session_maker, current_user.tenant_id, limit)}
