"""WhatsApp Cloud API (Meta Graph API) client. Direct httpx calls, no SDK, same pattern as the Twilio and Brevo clients.

Every function takes the business's own `phone_number_id` and `access_token` (each business pastes its own Meta credentials, so nothing here is a platform setting).
The token travels only in the Authorization header: never in a URL, a log line or an error message. Meta's own error text is shortened and the token is scrubbed from it before it
reaches a caller. Retries are bounded and conservative: a read is retried on a network error, a 429 or a 5xx; a SEND is retried only when Meta provably did not accept it
(429, 5xx, or a connection that never opened), because retrying a send that timed out after being accepted would message the person twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

GRAPH_VERSION = "v21.0"
GRAPH_BASE = "https://graph.facebook.com"
TIMEOUT_SECONDS = 15.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.5)
MAX_TEXT_CHARS = 4096  # WhatsApp's own limit for a text message body


class WhatsAppError(Exception):
    """A failed WhatsApp call. `code` is a short machine code; `str(error)` is safe to show an owner (no token, no phone number)."""

    code = "whatsapp_error"

    def __init__(self, message: str, *, status: int | None = None, meta_code: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.meta_code = meta_code


class WhatsAppAuthError(WhatsAppError):
    code = "whatsapp_auth_error"  # token expired, revoked, or missing a permission


class WhatsAppRateLimitError(WhatsAppError):
    code = "whatsapp_rate_limited"


class WhatsAppRejectedError(WhatsAppError):
    code = "whatsapp_rejected"  # Meta refused this message (bad number, template not approved, 24h window, ...)


class WhatsAppNetworkError(WhatsAppError):
    code = "whatsapp_network_error"


class WhatsAppServerError(WhatsAppError):
    code = "whatsapp_server_error"


def _client() -> httpx.AsyncClient:
    """One place that builds the HTTP client, so a test can replace it with a mock transport."""
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _scrub(text: str, token: str) -> str:
    if token:
        text = text.replace(token, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]+", r"\1[redacted]", text)
    text = re.sub(r"\bEA[A-Za-z0-9]{20,}\b", "[redacted]", text)  # the shape of a Meta access token, in case Meta ever echoes one
    return re.sub(r"[\r\n\x00-\x1f]", " ", text)[:300]


def _error_from(response: httpx.Response, token: str) -> WhatsAppError:
    meta_code: int | None = None
    message = ""
    try:
        err = (response.json() or {}).get("error") or {}
        meta_code = err.get("code") if isinstance(err.get("code"), int) else None
        message = str(err.get("error_user_msg") or err.get("message") or "")
    except ValueError:
        pass
    message = _scrub(message, token) or "no reason given"
    status = response.status_code
    text = f"WhatsApp (Meta) answered {status}" + (f" code {meta_code}" if meta_code is not None else "") + f": {message}"
    if status in (401, 403) or meta_code in (190, 200, 10) or (meta_code is not None and 200 <= meta_code <= 299):
        return WhatsAppAuthError(text, status=status, meta_code=meta_code)
    if status == 429 or meta_code in (4, 17, 80007, 130429, 131048, 131056):
        return WhatsAppRateLimitError(text, status=status, meta_code=meta_code)
    if status >= 500:
        return WhatsAppServerError(text, status=status, meta_code=meta_code)
    return WhatsAppRejectedError(text, status=status, meta_code=meta_code)


async def _request(method: str, path: str, token: str, *, json: dict | None = None, params: dict | None = None, idempotent: bool) -> dict[str, Any]:
    url = f"{GRAPH_BASE}/{GRAPH_VERSION}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    last: WhatsAppError | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            await _sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        try:
            async with _client() as client:
                response = await client.request(method, url, headers=headers, json=json, params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never reached Meta: safe to retry even a send
            last = WhatsAppNetworkError(f"Could not reach WhatsApp (Meta) ({type(exc).__name__})")
            continue
        except httpx.HTTPError as exc:
            last = WhatsAppNetworkError(f"WhatsApp (Meta) did not answer in time ({type(exc).__name__})")
            if idempotent:
                continue
            raise last from None
        if response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                raise WhatsAppServerError("WhatsApp (Meta) sent a reply that is not JSON", status=response.status_code) from None
            return body if isinstance(body, dict) else {}
        error = _error_from(response, token)
        if isinstance(error, (WhatsAppRateLimitError, WhatsAppServerError)) and response.status_code in (429, 500, 502, 503, 504):
            last = error
            continue
        raise error
    logger.warning("whatsapp_request_gave_up", path_kind=path.split("/")[-1][:20], error=last.code if last else None)
    assert last is not None
    raise last


async def get_phone_number(phone_number_id: str, access_token: str) -> dict[str, Any]:
    """Read-only: the number's display number, verified name and quality rating. The honest proof the id and token work together."""
    return await _request(
        "GET", f"/{phone_number_id}", access_token, params={"fields": "display_phone_number,verified_name,quality_rating"}, idempotent=True,
    )


def _message_id(body: dict[str, Any]) -> str:
    messages = body.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict) and messages[0].get("id"):
        return str(messages[0]["id"])
    raise WhatsAppServerError("WhatsApp (Meta) accepted the call but returned no message id")


async def send_text(phone_number_id: str, access_token: str, *, to: str, body: str) -> str:
    """A free-form text. Meta only delivers it inside the 24 hours after the person's last message to the business (the caller enforces that). Returns the message id."""
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text", "text": {"preview_url": False, "body": body[:MAX_TEXT_CHARS]}}
    return _message_id(await _request("POST", f"/{phone_number_id}/messages", access_token, json=payload, idempotent=False))


async def send_template(
    phone_number_id: str, access_token: str, *, to: str, name: str, language: str = "en_US", body_params: list[str] | tuple[str, ...] = (),
) -> str:
    """An approved message template (the only thing Meta delivers outside the 24 hour window). `body_params` fill the {{1}}, {{2}} ... slots of the template body."""
    template: dict[str, Any] = {"name": name, "language": {"code": language}}
    if body_params:
        template["components"] = [{"type": "body", "parameters": [{"type": "text", "text": str(p)[:1024]} for p in body_params]}]
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "template", "template": template}
    return _message_id(await _request("POST", f"/{phone_number_id}/messages", access_token, json=payload, idempotent=False))


async def mark_read(phone_number_id: str, access_token: str, *, message_id: str) -> bool:
    """Show the blue ticks for an inbound message. Safe to repeat."""
    body = await _request(
        "POST", f"/{phone_number_id}/messages", access_token,
        json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id}, idempotent=True,
    )
    return bool(body.get("success"))


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Meta signs every webhook POST: `X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body keyed with the app secret>`. Constant-time compare; False on anything odd."""
    if not signature_header or not app_secret or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):].strip().lower())
