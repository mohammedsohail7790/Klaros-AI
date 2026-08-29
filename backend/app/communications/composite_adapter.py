"""Phase 12C: routes email to SendGrid and SMS to Twilio independently —
either, both, or neither may be configured. Reuses InternalTestCommunicationAdapter
for whichever channel has no real provider configured, so a deployment with
only SENDGRID_API_KEY set still gets honest, logged SMS behavior (a real
failure status, not a silent success) rather than losing SMS entirely.
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate


class CompositeCommunicationAdapter(CommunicationProvider):
    provider_name = "composite"

    def __init__(
        self,
        session_factory: async_sessionmaker,
        email_provider: CommunicationProvider,
        sms_provider: CommunicationProvider,
    ) -> None:
        self._email_provider = email_provider
        self._sms_provider = sms_provider

    async def send_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate
    ) -> bool:
        return await self._email_provider.send_email(tenant_id, to=to, subject=subject, body=body, template=template)

    async def send_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate
    ) -> bool:
        return await self._sms_provider.send_sms(tenant_id, to=to, body=body, template=template)
