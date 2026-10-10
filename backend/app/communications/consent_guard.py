"""Consent guard in front of EVERY outbound message (app/communications/factory.py is the one place providers are built).

For a consent-gated tenant a message is sent only if its recipient resolves to a lead whose newest consent evidence grants `contact` (a recipient that
matches nobody is NOT sent to). Staff invitations are exempt: they go to team members, not to patients. Anything blocked returns False ("not sent",
the interface's own failure value) and is logged without the recipient. Every other tenant is untouched.
"""

from __future__ import annotations

import uuid

import structlog

from app.communications.base import CommunicationProvider, MessageTemplate

logger = structlog.get_logger()
_EXEMPT = {MessageTemplate.TEAM_INVITE}


class ConsentGuardedProvider(CommunicationProvider):
    def __init__(self, inner: CommunicationProvider, session_factory) -> None:
        self._inner = inner
        self._session_factory = session_factory
        self.provider_name = inner.provider_name

    async def _allowed(self, tenant_id: uuid.UUID, template: MessageTemplate, *, email: str | None = None, phone: str | None = None) -> bool:
        from app.services import consent_gate

        if template in _EXEMPT or not await consent_gate.tenant_requires_consent(self._session_factory, tenant_id):
            return True
        ok = await consent_gate.recipient_may_be_contacted(self._session_factory, tenant_id, email=email, phone=phone)
        if not ok:
            logger.warning("outbound_blocked_no_contact_consent", tenant_id=str(tenant_id), template=str(template), channel="email" if email else "sms")
        return ok

    async def send_email(self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate) -> bool:
        if not await self._allowed(tenant_id, template, email=to):
            return False
        return await self._inner.send_email(tenant_id, to=to, subject=subject, body=body, template=template)

    async def send_sms(self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate) -> bool:
        if not await self._allowed(tenant_id, template, phone=to):
            return False
        return await self._inner.send_sms(tenant_id, to=to, body=body, template=template)
