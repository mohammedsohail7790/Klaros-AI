"""Send one plain-text e-mail through Brevo's web API (HTTPS). Raises on any failure, so a caller can never mistake a failed send for a delivered one."""

import httpx

from app.communications.email_config import brevo_from_email
from app.core.config import get_settings

_SEND_URL = "https://api.brevo.com/v3/smtp/email"
_TIMEOUT_SECONDS = 15.0


def _client() -> httpx.AsyncClient:  # a function so tests can swap in a mock transport
    return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)


async def send_via_brevo(*, to: str, subject: str, body: str, from_name: str | None = None, reply_to: str | None = None, reply_to_name: str | None = None) -> str | None:
    """Returns Brevo's message id."""
    settings = get_settings()
    payload = {"sender": {"email": brevo_from_email()}, "to": [{"email": to}], "subject": subject, "textContent": body}
    if from_name:
        payload["sender"]["name"] = from_name
    if reply_to:
        payload["replyTo"] = {"email": reply_to, **({"name": reply_to_name} if reply_to_name else {})}
    async with _client() as client:
        response = await client.post(_SEND_URL, headers={"api-key": str(settings.BREVO_API_KEY), "accept": "application/json", "content-type": "application/json"}, json=payload)
        response.raise_for_status()
    try:
        return response.json().get("messageId")
    except ValueError:
        return None


_WEBHOOKS_URL = "https://api.brevo.com/v3/webhooks"


async def list_inbound_webhooks() -> list[dict]:
    """The inbound-email webhooks already registered on this Brevo account (id, url, domain)."""
    async with _client() as client:
        response = await client.get(_WEBHOOKS_URL, params={"type": "inbound"}, headers={"api-key": str(get_settings().BREVO_API_KEY), "accept": "application/json"})
        response.raise_for_status()
    return list(response.json().get("webhooks") or [])


async def create_inbound_webhook(*, url: str, domain: str) -> dict:
    """Ask Brevo to POST every reply received at `domain` to `url`. Returns Brevo's answer (its webhook id)."""
    async with _client() as client:
        response = await client.post(_WEBHOOKS_URL, headers={"api-key": str(get_settings().BREVO_API_KEY), "accept": "application/json", "content-type": "application/json"},
                                     json={"type": "inbound", "events": ["inboundEmailProcessed"], "url": url, "domain": domain, "description": "Klaros replies"})
        response.raise_for_status()
    return response.json()
