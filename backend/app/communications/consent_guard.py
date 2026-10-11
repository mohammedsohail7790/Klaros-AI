"""Consent guard in front of EVERY outbound message (app/communications/factory.py is the one place providers are built).

For a consent-gated tenant a message is sent only if consent for the patient it is ABOUT is established:
  * the recipient must resolve to exactly one patient, or the caller binds a validated `lead_id` / `customer_id` (an address shared by several patients
    is ambiguous and blocked); the newest evidence of that patient's leads must grant `contact` (see consent_gate.contact_decision);
  * a TEAM_INVITE skips patient consent ONLY when `invite_id` names a real, unexpired, PENDING invitation of this tenant addressed to that exact
    e-mail. The template name alone exempts nothing.
Blocked sends return SendResult(BLOCKED_CONSENT, reason) from `deliver_*`, and False from the legacy `send_*`. Blocks are logged without the recipient.
Every other tenant is untouched.
"""

from __future__ import annotations

import uuid

import structlog

from app.communications.base import CommunicationProvider, MessageTemplate, SendResult, SendStatus

logger = structlog.get_logger()
_BLOCKED = SendStatus.BLOCKED_CONSENT


class ConsentGuardedProvider(CommunicationProvider):
    def __init__(self, inner: CommunicationProvider, session_factory) -> None:
        self._inner = inner
        self._session_factory = session_factory
        self.provider_name = inner.provider_name

    async def _decide(
        self, tenant_id: uuid.UUID, template: MessageTemplate, *, email: str | None = None, phone: str | None = None,
        lead_id: uuid.UUID | None = None, customer_id: uuid.UUID | None = None, invite_id: uuid.UUID | None = None,
    ) -> tuple[bool, str]:
        from app.services import consent_gate

        if not await consent_gate.tenant_requires_consent(self._session_factory, tenant_id):
            return True, "not_gated"
        if template == MessageTemplate.TEAM_INVITE:
            if await consent_gate.is_genuine_pending_invitee(self._session_factory, tenant_id, invite_id, email):
                return True, "pending_invitee"
            logger.warning("outbound_blocked_invite_not_pending", tenant_id=str(tenant_id))
            return False, "invite_not_pending"
        try:
            ok, reason = await consent_gate.contact_decision(
                self._session_factory, tenant_id, email=email, phone=phone, lead_id=lead_id, customer_id=customer_id
            )
        except Exception:  # noqa: BLE001 - cannot establish consent: fail closed
            ok, reason = False, "consent_lookup_failed"
        if not ok:
            logger.warning("outbound_blocked_no_contact_consent", tenant_id=str(tenant_id), template=str(template), channel="email" if email else "sms", reason=reason)
        return ok, reason

    async def deliver_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate,
        customer_id: uuid.UUID | None = None, lead_id: uuid.UUID | None = None, invite_id: uuid.UUID | None = None,
    ) -> SendResult:
        ok, reason = await self._decide(tenant_id, template, email=to, lead_id=lead_id, customer_id=customer_id, invite_id=invite_id)
        if not ok:
            return SendResult(_BLOCKED, reason)
        sent = await self._inner.send_email(tenant_id, to=to, subject=subject, body=body, template=template)
        return SendResult(SendStatus.SENT if sent else SendStatus.FAILED)

    async def deliver_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate,
        customer_id: uuid.UUID | None = None, lead_id: uuid.UUID | None = None,
    ) -> SendResult:
        ok, reason = await self._decide(tenant_id, template, phone=to, lead_id=lead_id, customer_id=customer_id)
        if not ok:
            return SendResult(_BLOCKED, reason)
        sent = await self._inner.send_sms(tenant_id, to=to, body=body, template=template)
        return SendResult(SendStatus.SENT if sent else SendStatus.FAILED)

    async def send_email(self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate) -> bool:
        return (await self.deliver_email(tenant_id, to=to, subject=subject, body=body, template=template)).sent

    async def send_sms(self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate) -> bool:
        return (await self.deliver_sms(tenant_id, to=to, body=body, template=template)).sent
