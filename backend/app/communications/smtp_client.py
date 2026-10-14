"""Send one plain-text e-mail through the configured SMTP account. Raises on any failure, so a caller can never mistake a failed send for a delivered one."""

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

_TIMEOUT_SECONDS = 15.0


def _send_blocking(*, to: str, subject: str, body: str, from_name: str | None) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{s.SMTP_FROM_EMAIL}>" if from_name else str(s.SMTP_FROM_EMAIL)
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    if s.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(s.SMTP_HOST, s.SMTP_PORT, timeout=_TIMEOUT_SECONDS) as server:
            server.login(s.SMTP_USERNAME, s.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT, timeout=_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(s.SMTP_USERNAME, s.SMTP_PASSWORD)
            server.send_message(msg)


async def send_via_smtp(*, to: str, subject: str, body: str, from_name: str | None = None) -> None:
    await asyncio.to_thread(_send_blocking, to=to, subject=subject, body=body, from_name=from_name)
