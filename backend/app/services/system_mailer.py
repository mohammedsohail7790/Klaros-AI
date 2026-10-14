"""Transactional email Klaros itself sends to a person about their own account (password reset). This is NOT the customer-communication stack:
that one is tenant-scoped, template-driven and consent-guarded, and a password reset is none of those things.

Honest by construction: it sends through SendGrid only when SENDGRID_API_KEY and SENDGRID_FROM_EMAIL are configured, and otherwise reports that it did NOT
send (returns False and logs `system_email_not_configured`). It never pretends. Outside production the message is also logged so the flow can be exercised
locally; in production the body (which carries a secret link) is never logged.
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
_TIMEOUT_SECONDS = 15.0


def email_is_configured() -> bool:
    from app.communications.email_config import email_configured

    return email_configured()


async def send_system_email(*, to: str, subject: str, body: str) -> bool:
    """Returns True only when the provider accepted the message."""
    settings = get_settings()
    if not email_is_configured():
        logger.warning("system_email_not_configured", subject=subject)
        if settings.ENV != "production":
            logger.info("system_email_dev_outbox", to=to, subject=subject, body=body)
        return False
    from app.communications.email_config import sendgrid_configured

    if not sendgrid_configured():  # an SMTP account is configured instead
        from app.communications.smtp_client import send_via_smtp

        try:
            await send_via_smtp(to=to, subject=subject, body=body, from_name="Klaros AI")
        except Exception as exc:  # noqa: BLE001
            logger.warning("system_email_failed", subject=subject, error_type=type(exc).__name__)
            return False
        return True
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": settings.SENDGRID_FROM_EMAIL, "name": "Klaros AI"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(_SEND_URL, headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"}, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("system_email_failed", subject=subject, error_type=type(exc).__name__)
        return False
    return True
