"""`POST /api/v1/webhooks/inbound-email/{secret}` (the email provider hands a parsed reply to Klaros) and `GET /api/v1/inbox/replies` (the team reads them).

The webhook has no login: its only protection is the secret in its address, compared in constant time. With no secret configured, or a wrong one, it answers 404 as if it did not exist.
Whatever a stranger writes in an email is untrusted: it is stored as text, never executed, and cut to a fixed length.
"""

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.core.rate_limit import rate_limit
from app.db.session import async_session_maker
from app.services import inbound_email_service

router = APIRouter(tags=["inbound-email"])
MAX_BODY_BYTES = 3 * 1024 * 1024


@router.post("/webhooks/inbound-email/{secret}", dependencies=[Depends(rate_limit("inbound_email", limit_setting="RATE_LIMIT_INBOUND_EMAIL_PER_MINUTE", window_seconds=60))])
async def inbound_email_webhook(secret: str, request: Request) -> dict[str, Any]:
    expected = get_settings().INBOUND_EMAIL_WEBHOOK_SECRET
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()) or inbound_email_service.reply_domain() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Too large")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Not JSON") from None
    return await inbound_email_service.handle_payload(async_session_maker, payload)


@router.get("/inbox/replies")
async def list_replies(limit: int = 50, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"replies": await inbound_email_service.list_replies(async_session_maker, current_user.tenant_id, limit)}
