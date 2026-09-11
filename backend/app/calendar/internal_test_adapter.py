"""INTERNAL TEST CALENDAR (section 13).

A real, working calendar backed by the `appointments` table — not a mock.
Booking, availability, cancellation, and double-booking prevention all
actually execute against Postgres (sqlite in tests). It stands in for a real
external calendar provider (Google Calendar, Outlook) until one is
connected; every response makes clear this is the internal test calendar.

Business hours are fixed at 09:00-17:00 UTC, every day, in 30-minute slots —
a deliberately simple default. Per-tenant business hours/timezones are a
natural Phase 4 addition (they'd belong next to the service catalog /
service-area config scoring.py already flags as missing), not implemented here.
"""

import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.calendar.base import BookingRequest, CalendarProvider, DoubleBookingError, TimeSlot
from app.models.crm import Appointment, AppointmentStatus, Customer

BUSINESS_START = time(9, 0)
BUSINESS_END = time(17, 0)
ACTIVE_STATUSES = (AppointmentStatus.TENTATIVE, AppointmentStatus.CONFIRMED)


def _naive_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip (Postgres doesn't), so any datetime
    that may have come back from the database gets normalized to naive-UTC
    before comparison — every datetime in this module is UTC regardless."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return _naive_utc(a_start) < _naive_utc(b_end) and _naive_utc(a_end) > _naive_utc(b_start)


class InternalTestCalendarAdapter(CalendarProvider):
    provider_name = "internal_test_calendar"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_availability(
        self,
        tenant_id: uuid.UUID,
        *,
        date_from: datetime,
        date_to: datetime,
        duration_minutes: int,
        assigned_user_id: uuid.UUID | None = None,
    ) -> list[TimeSlot]:
        async with self._session_factory() as session:
            query = select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.status.in_(ACTIVE_STATUSES),
                Appointment.start_time < date_to,
                Appointment.end_time > date_from,
            )
            if assigned_user_id is not None:
                query = query.where(Appointment.assigned_user_id == assigned_user_id)
            busy = (await session.execute(query)).scalars().all()

        slots: list[TimeSlot] = []
        duration = timedelta(minutes=duration_minutes)
        cursor = date_from
        while cursor + duration <= date_to:
            slot_end = cursor + duration
            within_hours = BUSINESS_START <= cursor.time() < BUSINESS_END
            if within_hours and not any(_overlaps(cursor, slot_end, b.start_time, b.end_time) for b in busy):
                slots.append(TimeSlot(start_time=cursor, end_time=slot_end))
            cursor += timedelta(minutes=30)

        return slots

    async def create_event(self, request: BookingRequest) -> Appointment:
        async with self._session_factory() as session:
            customer = await session.get(Customer, request.customer_id)
            if customer is None or customer.tenant_id != request.tenant_id:
                raise ValueError("Customer not found")

            if request.idempotency_key:
                existing = (
                    await session.execute(
                        select(Appointment).where(
                            Appointment.tenant_id == request.tenant_id,
                            Appointment.idempotency_key == request.idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing

            # Phase 7: a real PostgreSQL concurrency test proved the plain
            # check-then-insert below is NOT race-safe — two simultaneous
            # bookers for the exact same slot could both pass the SELECT
            # before either INSERT commits (a classic phantom-read race:
            # `SELECT ... FOR UPDATE` can't lock rows that don't exist
            # yet). SQLite masked this because this codebase gives each
            # writer its own serialized connection to the same file (see
            # app/db/session.py's comment). The real fix is a PostgreSQL
            # session-level advisory lock, keyed on exactly the resource
            # this booking would contend over (tenant + assigned technician,
            # or tenant alone when unassigned) — acquired BEFORE the
            # conflict check and released automatically at transaction end,
            # so only one concurrent booker for that resource ever reaches
            # the check-then-insert at a time. A no-op on SQLite (which
            # doesn't have advisory locks and doesn't need one here).
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_key = f"appointment-booking:{request.tenant_id}:{request.assigned_user_id or 'unassigned'}"
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})

            conflict_query = select(Appointment).where(
                Appointment.tenant_id == request.tenant_id,
                Appointment.status.in_(ACTIVE_STATUSES),
                Appointment.start_time < request.end_time,
                Appointment.end_time > request.start_time,
            )
            if request.assigned_user_id is not None:
                conflict_query = conflict_query.where(
                    Appointment.assigned_user_id == request.assigned_user_id
                )
            conflict = (await session.execute(conflict_query)).scalars().first()
            if conflict is not None:
                raise DoubleBookingError(
                    f"Slot {request.start_time.isoformat()} conflicts with appointment {conflict.id}"
                )

            appointment = Appointment(
                tenant_id=request.tenant_id,
                lead_id=request.lead_id,
                customer_id=request.customer_id,
                assigned_user_id=request.assigned_user_id,
                title=request.title,
                service=request.service,
                location=request.location,
                start_time=request.start_time,
                end_time=request.end_time,
                status=AppointmentStatus.TENTATIVE,
                notes=request.notes,
                idempotency_key=request.idempotency_key,
            )
            session.add(appointment)
            await session.commit()
            await session.refresh(appointment)
            return appointment

    async def update_event(self, tenant_id: uuid.UUID, appointment_id: uuid.UUID, **changes) -> Appointment:
        async with self._session_factory() as session:
            appointment = await session.get(Appointment, appointment_id)
            if appointment is None or appointment.tenant_id != tenant_id:
                raise ValueError("Appointment not found")

            new_start = changes.get("start_time", appointment.start_time)
            new_end = changes.get("end_time", appointment.end_time)
            if "start_time" in changes or "end_time" in changes:
                conflict_query = select(Appointment).where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.id != appointment_id,
                    Appointment.status.in_(ACTIVE_STATUSES),
                    Appointment.start_time < new_end,
                    Appointment.end_time > new_start,
                )
                if appointment.assigned_user_id is not None:
                    conflict_query = conflict_query.where(
                        Appointment.assigned_user_id == appointment.assigned_user_id
                    )
                conflict = (await session.execute(conflict_query)).scalars().first()
                if conflict is not None:
                    raise DoubleBookingError(
                        f"Reschedule conflicts with appointment {conflict.id}"
                    )

            for field, value in changes.items():
                setattr(appointment, field, value)
            await session.commit()
            await session.refresh(appointment)
            return appointment

    async def cancel_event(self, tenant_id: uuid.UUID, appointment_id: uuid.UUID) -> Appointment:
        return await self.update_event(tenant_id, appointment_id, status=AppointmentStatus.CANCELLED)
