"""section 2/3: job creation.

    Appointment -> Create Job -> job.created -> job enters the pipeline

Idempotent: creating a job from the same appointment twice returns the
original job rather than creating a duplicate (mirrors the pattern already
used for leads/events in Phase 2/3).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Appointment
from app.models.event import EventType
from app.models.operations import Job, JobPriority, JobStatus


class JobNotFoundError(Exception):
    pass


@dataclass
class CreateJobInput:
    title: str
    customer_id: uuid.UUID
    lead_id: uuid.UUID | None = None
    appointment_id: uuid.UUID | None = None
    quote_id: uuid.UUID | None = None
    description: str | None = None
    service_type: str | None = None
    priority: str = JobPriority.NORMAL
    location: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_duration_minutes: int | None = None
    estimated_revenue: float | None = None
    estimated_cost: float | None = None
    idempotency_key: str | None = None


class JobService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def _next_job_number(self, session, tenant_id: uuid.UUID) -> str:
        count = (
            await session.execute(select(func.count(Job.id)).where(Job.tenant_id == tenant_id))
        ).scalar_one()
        return f"JOB-{1000 + count + 1}"

    async def create_job(self, tenant_id: uuid.UUID, data: CreateJobInput) -> tuple[Job, bool]:
        idempotency_key = data.idempotency_key or (
            f"job-from-appointment-{data.appointment_id}" if data.appointment_id else None
        )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            if idempotency_key:
                existing = (
                    await session.execute(
                        select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True

            estimated_margin = None
            if data.estimated_revenue is not None and data.estimated_cost is not None:
                estimated_margin = float(data.estimated_revenue) - float(data.estimated_cost)

            job = Job(
                tenant_id=tenant_id,
                customer_id=data.customer_id,
                lead_id=data.lead_id,
                appointment_id=data.appointment_id,
                quote_id=data.quote_id,
                job_number=await self._next_job_number(session, tenant_id),
                title=data.title,
                description=data.description,
                service_type=data.service_type,
                status=JobStatus.DRAFT,
                priority=data.priority,
                location=data.location,
                scheduled_start=data.scheduled_start,
                scheduled_end=data.scheduled_end,
                estimated_duration_minutes=data.estimated_duration_minutes,
                estimated_revenue=data.estimated_revenue,
                estimated_cost=data.estimated_cost,
                estimated_margin=estimated_margin,
                idempotency_key=idempotency_key,
            )
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                # Phase 22 fix: a concurrent `create_job` call for the SAME
                # idempotency_key (e.g. two Stripe webhook deliveries for
                # the same quote deposit racing, or any other doubled
                # trigger) can pass the "does a job with this key already
                # exist?" check above before either commits — the second
                # commit then hits the real `uq_jobs_tenant_idempotency_key`
                # constraint. Previously this raised an unhandled
                # IntegrityError straight out of `create_job`, which (via
                # `QuoteService._convert_to_job`) could leave a quote
                # stuck at DEPOSIT_PAID with NO Job ever created despite a
                # real Stripe deposit having been paid — confirmed by
                # direct reproduction under `asyncio.gather`. Mirrors the
                # exact "concurrent delivery raced us to the unique
                # constraint — the other request is handling it, this is
                # a genuine duplicate, not an error" pattern already used
                # for `WebhookEvent` (app/api/v1/webhooks.py) and
                # `Payment` (implicitly, via its own pre-insert existence
                # check) elsewhere in this codebase.
                await session.rollback()
                if idempotency_key:
                    # A fresh session for the re-check — reusing the one
                    # that just failed its commit risks stale/poisoned
                    # session state rather than a genuinely fresh read of
                    # what actually got committed.
                    async with self._session_factory() as fresh_session:
                        await set_tenant_context(fresh_session, tenant_id)
                        existing = (
                            await fresh_session.execute(
                                select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key)
                            )
                        ).scalar_one_or_none()
                    if existing is not None:
                        return existing, True
                raise
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_CREATED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id), "job_number": job.job_number},
            idempotency_key=f"job-created-{job.id}",
        )
        return job, False

    async def create_job_from_appointment(
        self, tenant_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> tuple[Job, bool]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            appointment = await session.get(Appointment, appointment_id)
            if appointment is None or appointment.tenant_id != tenant_id:
                raise JobNotFoundError("Appointment not found")

        return await self.create_job(
            tenant_id,
            CreateJobInput(
                title=appointment.title,
                customer_id=appointment.customer_id,
                lead_id=appointment.lead_id,
                appointment_id=appointment.id,
                service_type=appointment.service,
                location=appointment.location,
                scheduled_start=appointment.start_time,
                scheduled_end=appointment.end_time,
            ),
        )
