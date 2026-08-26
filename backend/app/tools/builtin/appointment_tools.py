import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.calendar.base import BookingRequest, CalendarProvider, DoubleBookingError
from app.events.bus import EventBus
from app.models.crm import Appointment
from app.models.event import EventType
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


def _appointment_to_dict(a: Appointment) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "lead_id": str(a.lead_id) if a.lead_id else None,
        "customer_id": str(a.customer_id),
        "assigned_user_id": str(a.assigned_user_id) if a.assigned_user_id else None,
        "title": a.title,
        "service": a.service,
        "location": a.location,
        "start_time": a.start_time.isoformat(),
        "end_time": a.end_time.isoformat(),
        "status": a.status,
        "notes": a.notes,
    }


class CheckAvailabilityInput(BaseModel):
    date_from: datetime
    date_to: datetime
    duration_minutes: int = 60
    assigned_user_id: uuid.UUID | None = None


class CheckAvailabilityOutput(BaseModel):
    calendar_provider: str
    slots: list[dict[str, str]]


class CheckAvailability(Tool):
    name = "crm.check_availability"
    description = "List open slots on the (internal test) calendar."
    input_schema = CheckAvailabilityInput
    output_schema = CheckAvailabilityOutput
    required_permission = Permission.READ_APPOINTMENTS

    def __init__(self, calendar: CalendarProvider) -> None:
        self._calendar = calendar

    async def execute(self, input: CheckAvailabilityInput, context: ExecutionContext) -> CheckAvailabilityOutput:
        slots = await self._calendar.get_availability(
            context.tenant_id,
            date_from=input.date_from,
            date_to=input.date_to,
            duration_minutes=input.duration_minutes,
            assigned_user_id=input.assigned_user_id,
        )
        return CheckAvailabilityOutput(
            calendar_provider=self._calendar.provider_name,
            slots=[{"start_time": s.start_time.isoformat(), "end_time": s.end_time.isoformat()} for s in slots],
        )


class CreateAppointmentInput(BaseModel):
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


class AppointmentOutput(BaseModel):
    appointment: dict[str, Any]


class CreateAppointment(Tool):
    name = "crm.create_appointment"
    description = "Book an appointment on the (internal test) calendar, rejecting double bookings."
    input_schema = CreateAppointmentInput
    output_schema = AppointmentOutput
    required_permission = Permission.CREATE_APPOINTMENT

    def __init__(self, calendar: CalendarProvider, bus: EventBus) -> None:
        self._calendar = calendar
        self._bus = bus

    async def execute(self, input: CreateAppointmentInput, context: ExecutionContext) -> AppointmentOutput:
        try:
            appointment = await self._calendar.create_event(
                BookingRequest(
                    tenant_id=context.tenant_id,
                    customer_id=input.customer_id,
                    title=input.title,
                    start_time=input.start_time,
                    end_time=input.end_time,
                    lead_id=input.lead_id,
                    assigned_user_id=input.assigned_user_id,
                    service=input.service,
                    location=input.location,
                    notes=input.notes,
                    idempotency_key=input.idempotency_key,
                )
            )
        except DoubleBookingError as exc:
            raise ValueError(str(exc)) from exc

        await self._bus.publish(
            tenant_id=context.tenant_id,
            event_type=EventType.APPOINTMENT_CREATED,
            source="crm",
            entity_type="appointment",
            entity_id=appointment.id,
            payload={"appointment_id": str(appointment.id)},
            correlation_id=context.correlation_id,
            idempotency_key=f"appointment-created-{appointment.id}",
        )
        return AppointmentOutput(appointment=_appointment_to_dict(appointment))


class CancelAppointmentInput(BaseModel):
    appointment_id: uuid.UUID


class CancelAppointment(Tool):
    name = "crm.cancel_appointment"
    description = "Cancel an appointment."
    input_schema = CancelAppointmentInput
    output_schema = AppointmentOutput
    required_permission = Permission.CANCEL_APPOINTMENT

    def __init__(self, calendar: CalendarProvider, bus: EventBus) -> None:
        self._calendar = calendar
        self._bus = bus

    async def execute(self, input: CancelAppointmentInput, context: ExecutionContext) -> AppointmentOutput:
        appointment = await self._calendar.cancel_event(context.tenant_id, input.appointment_id)
        await self._bus.publish(
            tenant_id=context.tenant_id,
            event_type=EventType.APPOINTMENT_CANCELLED,
            source="crm",
            entity_type="appointment",
            entity_id=appointment.id,
            payload={"appointment_id": str(appointment.id)},
            correlation_id=context.correlation_id,
        )
        return AppointmentOutput(appointment=_appointment_to_dict(appointment))


class RescheduleAppointmentInput(BaseModel):
    appointment_id: uuid.UUID
    start_time: datetime
    end_time: datetime


class RescheduleAppointment(Tool):
    name = "crm.reschedule_appointment"
    description = "Move an appointment to a new time, rejecting a conflicting slot."
    input_schema = RescheduleAppointmentInput
    output_schema = AppointmentOutput
    required_permission = Permission.CREATE_APPOINTMENT

    def __init__(self, calendar: CalendarProvider, bus: EventBus) -> None:
        self._calendar = calendar
        self._bus = bus

    async def execute(self, input: RescheduleAppointmentInput, context: ExecutionContext) -> AppointmentOutput:
        try:
            appointment = await self._calendar.update_event(
                context.tenant_id,
                input.appointment_id,
                start_time=input.start_time,
                end_time=input.end_time,
            )
        except DoubleBookingError as exc:
            raise ValueError(str(exc)) from exc

        await self._bus.publish(
            tenant_id=context.tenant_id,
            event_type=EventType.APPOINTMENT_UPDATED,
            source="crm",
            entity_type="appointment",
            entity_id=appointment.id,
            payload={"appointment_id": str(appointment.id)},
            correlation_id=context.correlation_id,
        )
        return AppointmentOutput(appointment=_appointment_to_dict(appointment))
