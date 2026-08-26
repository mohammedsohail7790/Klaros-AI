"""INTERNAL TEST communication adapter — logs every message to
`communication_logs` (real, persisted, queryable) instead of calling
Gmail/Twilio/SendGrid, none of which are connected (see
app/integrations/adapters.py — GmailAdapter/TwilioAdapter/SendGridAdapter
all report NOT_CONNECTED)."""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.models.communication import CommunicationLog

logger = structlog.get_logger(__name__)


class InternalTestCommunicationAdapter(CommunicationProvider):
    provider_name = "internal_test_communication"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def send_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate
    ) -> bool:
        await self._log(tenant_id, channel="EMAIL", template=template, recipient=to, subject=subject, body=body)
        logger.info("internal_test_email_sent", to=to, template=template.value, subject=subject)
        return True

    async def send_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate
    ) -> bool:
        await self._log(tenant_id, channel="SMS", template=template, recipient=to, subject=None, body=body)
        logger.info("internal_test_sms_sent", to=to, template=template.value)
        return True

    async def _log(self, tenant_id, *, channel, template, recipient, subject, body) -> None:
        async with self._session_factory() as session:
            session.add(
                CommunicationLog(
                    tenant_id=tenant_id,
                    channel=channel,
                    template=template.value,
                    recipient=recipient,
                    subject=subject,
                    body=body,
                    status="SENT",
                    provider=self.provider_name,
                )
            )
            await session.commit()
