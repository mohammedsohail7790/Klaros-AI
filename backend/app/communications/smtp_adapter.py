"""Real e-mail delivery through any SMTP account (Brevo, Gmail, Zoho, ...), as an alternative to SendGrid. Same contract as the SendGrid adapter: every send, successful or not,
is written to CommunicationLog, and a failure returns False. It sends e-mail only; SMS is not its job and reports an honest failure."""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.communications.sender_name import sender_name_for
from app.communications.smtp_client import send_via_smtp
from app.db.session import set_tenant_context
from app.models.communication import CommunicationLog

logger = structlog.get_logger(__name__)


class SmtpEmailAdapter(CommunicationProvider):
    provider_name = "smtp"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    def __repr__(self) -> str:
        return f"{type(self).__name__}(is_connected=True)"

    async def send_email(self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate) -> bool:
        status = "SENT"
        try:
            await send_via_smtp(to=to, subject=subject, body=body, from_name=await sender_name_for(self._session_factory, tenant_id))
            logger.info("smtp_email_sent", template=template.value)
        except Exception as exc:  # noqa: BLE001 - any SMTP or network failure is a failed send, never a delivered one
            status = "FAILED_SMTP"
            logger.warning("smtp_email_failed", template=template.value, error_type=type(exc).__name__)
        await self._log(tenant_id, channel="EMAIL", template=template, recipient=to, subject=subject, body=body, status=status)
        return status == "SENT"

    async def send_sms(self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate) -> bool:
        await self._log(tenant_id, channel="SMS", template=template, recipient=to, subject=None, body=body, status="FAILED_NO_SMS_PROVIDER")
        return False

    async def _log(self, tenant_id, *, channel, template, recipient, subject, body, status) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            session.add(CommunicationLog(tenant_id=tenant_id, channel=channel, template=template.value, recipient=recipient, external_id=None, subject=subject, body=body,
                                         status=status, provider=self.provider_name))
            await session.commit()
