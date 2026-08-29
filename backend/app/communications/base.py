"""section 16: communication provider abstraction."""

import uuid
from abc import ABC, abstractmethod
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
