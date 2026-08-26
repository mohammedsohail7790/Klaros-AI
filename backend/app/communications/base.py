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
