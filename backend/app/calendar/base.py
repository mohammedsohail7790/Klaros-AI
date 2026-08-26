"""section 13: calendar provider abstraction.

Business logic (booking API, AI booking recommendation) calls this
interface, never a calendar SDK directly — the same provider-adapter
pattern as app/integrations/.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TimeSlot:
    start_time: datetime
    end_time: datetime


@dataclass
class BookingRequest:
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    title: str
    start_time: datetime
    end_time: datetime
    lead_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    service: str | None = None
    location: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None


class DoubleBookingError(Exception):
    pass


class CalendarProvider(ABC):
    provider_name: str

    @abstractmethod
    async def get_availability(
        self,
        tenant_id: uuid.UUID,
        *,
        date_from: datetime,
        date_to: datetime,
        duration_minutes: int,
        assigned_user_id: uuid.UUID | None = None,
    ) -> list[TimeSlot]: ...

    @abstractmethod
    async def create_event(self, request: BookingRequest): ...

    @abstractmethod
    async def update_event(self, tenant_id: uuid.UUID, appointment_id: uuid.UUID, **changes): ...

    @abstractmethod
    async def cancel_event(self, tenant_id: uuid.UUID, appointment_id: uuid.UUID): ...


class NotConnectedCalendarAdapter(CalendarProvider):
    """Google Calendar / Outlook / etc. — no OAuth configured, so every
    method fails loudly rather than pretending to book something."""

    provider_name = "external_calendar"

    async def get_availability(self, tenant_id, *, date_from, date_to, duration_minutes, assigned_user_id=None):
        raise NotImplementedError("External calendar provider is NOT_CONNECTED")

    async def create_event(self, request: BookingRequest):
        raise NotImplementedError("External calendar provider is NOT_CONNECTED")

    async def update_event(self, tenant_id, appointment_id, **changes):
        raise NotImplementedError("External calendar provider is NOT_CONNECTED")

    async def cancel_event(self, tenant_id, appointment_id):
        raise NotImplementedError("External calendar provider is NOT_CONNECTED")
