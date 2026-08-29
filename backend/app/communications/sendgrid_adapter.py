"""Real SendGrid email delivery (Phase 12C).

Direct httpx calls against SendGrid's v3 Mail Send API, matching this
project's established pattern for real external HTTP integrations (see
app/services/ai_provider.py) rather than adding the sendgrid SDK as a new
dependency. Every send — success or failure — is persisted to
CommunicationLog so the audit trail is identical in shape whether the
underlying provider is real or the internal-test adapter.
"""

import uuid

import httpx
import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.models.communication import CommunicationLog

logger = structlog.get_logger(__name__)

_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
_TIMEOUT_SECONDS = 15.0


class SendGridEmailAdapter(CommunicationProvider):
    """Only ever constructed when SENDGRID_API_KEY is actually set — see
    app/communications/factory.py. Real SMS is NOT this adapter's job
    (SendGrid doesn't do SMS); send_sms here always returns False so a
    caller relying on this alone for SMS gets an honest failure, not a
    silent no-op success."""

    provider_name = "sendgrid"

    def __init__(self, session_factory: async_sessionmaker, api_key: str, from_email: str) -> None:
        self._session_factory = session_factory
        self._api_key = api_key
        self._from_email = from_email

    def __repr__(self) -> str:
        return f"{type(self).__name__}(is_connected=True)"

    async def send_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate
    ) -> bool:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self._from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        status = "SENT"
        external_id: str | None = None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    _SEND_URL,
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
            # SendGrid returns the message id in this response header, not
            # the (empty, 202) body.
            external_id = response.headers.get("X-Message-Id")
            logger.info("sendgrid_email_sent", to=to, template=template.value, status_code=response.status_code, message_id=external_id)
        except httpx.HTTPStatusError as exc:
            status = f"FAILED_HTTP_{exc.response.status_code}"
            logger.warning("sendgrid_email_failed", to=to, template=template.value, status_code=exc.response.status_code)
        except httpx.HTTPError as exc:
            status = "FAILED_NETWORK"
            logger.warning("sendgrid_email_failed", to=to, template=template.value, error=str(exc))

        await self._log(
            tenant_id, channel="EMAIL", template=template, recipient=to, subject=subject, body=body,
            status=status, external_id=external_id,
        )
        return status == "SENT"

    async def send_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate
    ) -> bool:
        await self._log(
            tenant_id, channel="SMS", template=template, recipient=to, subject=None, body=body,
            status="FAILED_NO_SMS_PROVIDER",
        )
        return False

    async def _log(self, tenant_id, *, channel, template, recipient, subject, body, status, external_id=None) -> None:
        async with self._session_factory() as session:
            session.add(
                CommunicationLog(
                    tenant_id=tenant_id,
                    channel=channel,
                    template=template.value,
                    recipient=recipient,
                    external_id=external_id,
                    subject=subject,
                    body=body,
                    status=status,
                    provider=self.provider_name,
                )
            )
            await session.commit()
