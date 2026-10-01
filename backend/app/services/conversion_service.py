"""section 4: explicit lead -> customer -> appointment -> job conversion.

Phase 3 left lead -> customer -> appointment reachable but not automatic —
a lead and a customer created independently stayed separate unless matched
by email/phone at creation time. This service makes the full chain an
explicit, single, idempotent operation: booking a qualified lead.

    lead -> (match or create) customer -> appointment -> job

Never creates a duplicate customer — reuses the exact-match logic from
Phase 3 (`services/customer_matching.py`) exactly as before; still no fuzzy
auto-merge.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.calendar.base import BookingRequest, CalendarProvider
from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Customer, CustomerStatus, Lead, LeadStatus
from app.models.event import EventType
from app.models.operations import Job
from app.services.customer_matching import find_matching_customer, normalize_email, normalize_phone
from app.services.job_service import CreateJobInput, JobService


class LeadNotFoundError(Exception):
    pass


@dataclass
class ConversionResult:
    lead: Lead
    customer: Customer
    appointment_id: uuid.UUID
    job: Job
    customer_created: bool
    deduplicated: bool


class LeadConversionService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        bus: EventBus,
        calendar: CalendarProvider,
        job_service: JobService,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._calendar = calendar
        self._job_service = job_service

    async def convert_and_book(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        *,
        title: str,
        start_time: datetime,
        end_time: datetime,
        assigned_user_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> ConversionResult:
        idempotency_key = idempotency_key or f"lead-conversion-{lead_id}"

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await session.get(Lead, lead_id)
            if lead is None or lead.tenant_id != tenant_id:
                raise LeadNotFoundError("Lead not found")

            customer_created = False
            if lead.customer_id is not None:
                customer = await session.get(Customer, lead.customer_id)
            else:
                customer = await find_matching_customer(
                    session, tenant_id=tenant_id, email=lead.email, phone=lead.phone
                )
                if customer is None:
                    customer = Customer(
                        tenant_id=tenant_id,
                        name=lead.name,
                        email=normalize_email(lead.email),
                        phone=lead.phone,
                        phone_normalized=normalize_phone(lead.phone),
                        status=CustomerStatus.ACTIVE,
                    )
                    session.add(customer)
                    await session.flush()
                    customer_created = True
                lead.customer_id = customer.id

            lead.status = LeadStatus.CONVERTED
            await session.commit()
            await session.refresh(lead)
            await session.refresh(customer)

        if customer_created:
            await self._bus.publish(
                tenant_id=tenant_id,
                event_type=EventType.CUSTOMER_CREATED,
                source="operations.conversion",
                entity_type="customer",
                entity_id=customer.id,
                payload={"customer_id": str(customer.id), "converted_from_lead_id": str(lead_id)},
            )

        appointment = await self._calendar.create_event(
            BookingRequest(
                tenant_id=tenant_id,
                customer_id=customer.id,
                lead_id=lead_id,
                title=title,
                start_time=start_time,
                end_time=end_time,
                assigned_user_id=assigned_user_id,
                idempotency_key=f"{idempotency_key}-appointment",
            )
        )
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.APPOINTMENT_CREATED,
            source="operations.conversion",
            entity_type="appointment",
            entity_id=appointment.id,
            payload={"appointment_id": str(appointment.id)},
            idempotency_key=f"appointment-created-{appointment.id}",
        )

        job, job_deduplicated = await self._job_service.create_job_from_appointment(tenant_id, appointment.id)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.LEAD_CONVERTED,
            source="operations.conversion",
            entity_type="lead",
            entity_id=lead_id,
            payload={
                "lead_id": str(lead_id),
                "customer_id": str(customer.id),
                "appointment_id": str(appointment.id),
                "job_id": str(job.id),
            },
            idempotency_key=idempotency_key,
        )

        return ConversionResult(
            lead=lead,
            customer=customer,
            appointment_id=appointment.id,
            job=job,
            customer_created=customer_created,
            deduplicated=job_deduplicated,
        )
