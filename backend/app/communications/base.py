"""section 16: communication provider abstraction."""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class MessageTemplate(StrEnum):
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"
    APPOINTMENT_REMINDER = "appointment_reminder"
    APPOINTMENT_CANCELLATION = "appointment_cancellation"
    APPOINTMENT_RESCHEDULE = "appointment_reschedule"
    LEAD_FOLLOW_UP = "lead_follow_up"
    INVOICE_SENT = "invoice_sent"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_FAILED = "payment_failed"
    COLLECTION_REMINDER = "collection_reminder"
    OUTBOUND_SEQUENCE_STEP = "outbound_sequence_step"
    NURTURE_MESSAGE = "nurture_message"
    REACTIVATION_OUTREACH = "reactivation_outreach"
    POST_JOB_FOLLOWUP = "post_job_followup"
    REVIEW_REQUEST = "review_request"
    SERVICE_REMINDER = "service_reminder"
    WIN_BACK = "win_back"
    REFERRAL_INVITATION = "referral_invitation"
    SERVICE_RECOVERY = "service_recovery"
    TEAM_INVITE = "team_invite"


class SendStatus(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"  # the provider could not deliver (retryable)
    BLOCKED_CONSENT = "BLOCKED_CONSENT"  # refused by the consent guard (NOT retryable until consent evidence changes; never "delivered")


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str | None = None  # a short machine code, never recipient data

    @property
    def sent(self) -> bool:
        return self.status == SendStatus.SENT

    @property
    def blocked(self) -> bool:
        return self.status == SendStatus.BLOCKED_CONSENT


class CommunicationProvider(ABC):
    provider_name: str

    @abstractmethod
    async def send_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate
    ) -> bool: ...

    @abstractmethod
    async def send_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate
    ) -> bool: ...

    async def deliver_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate,
        customer_id: uuid.UUID | None = None, lead_id: uuid.UUID | None = None, invite_id: uuid.UUID | None = None,
    ) -> SendResult:
        """Identity-aware send. `customer_id` / `lead_id` bind the message to the patient it is ABOUT; `invite_id` binds a team invitation. The base
        implementation ignores them; ConsentGuardedProvider validates them. Returns a SendResult instead of a bare bool."""
        ok = await self.send_email(tenant_id, to=to, subject=subject, body=body, template=template)
        return SendResult(SendStatus.SENT if ok else SendStatus.FAILED)

    async def deliver_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate,
        customer_id: uuid.UUID | None = None, lead_id: uuid.UUID | None = None,
    ) -> SendResult:
        ok = await self.send_sms(tenant_id, to=to, body=body, template=template)
        return SendResult(SendStatus.SENT if ok else SendStatus.FAILED)
