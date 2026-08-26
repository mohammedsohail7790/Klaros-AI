"""section 6/11: job state transitions, assignment, dispatch.

Every transition here is validated against `job_state_machine.py`'s fixed
table before it's applied — nothing here silently allows an invalid jump.
Each method publishes exactly the event the spec names for that step and
returns the updated Job row.

Tool naming note: the spec's tool list (sections 5/30) doesn't name explicit
tools for the EN_ROUTE/ON_SITE steps in the DISPATCHED -> ... -> IN_PROGRESS
chain, even though the state machine (section 6) and event list (section 7)
require them as distinct steps. `update_job_status` below is the generic
transition used for those two; `dispatch_job`/`start_job`/`complete_job`/
`block_job`/`unblock_job`/`cancel_job` are the explicitly-named ones.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.operations import (
    ExceptionSeverity,
    ExceptionStatus,
    ExceptionType,
    Job,
    JobStatus,
    OperationsException,
    Worker,
    WorkerStatus,
)
from app.services.job_state_machine import validate_transition

_STATUS_TO_EVENT: dict[str, EventType] = {
    JobStatus.SCHEDULED: EventType.JOB_SCHEDULED,
    JobStatus.DISPATCHED: EventType.JOB_DISPATCHED,
    JobStatus.EN_ROUTE: EventType.JOB_EN_ROUTE,
    JobStatus.ON_SITE: EventType.JOB_ON_SITE,
    JobStatus.IN_PROGRESS: EventType.JOB_STARTED,
    JobStatus.QA_PENDING: EventType.JOB_UPDATED,
    JobStatus.COMPLETED: EventType.JOB_COMPLETED,
    JobStatus.CLOSED: EventType.JOB_CLOSED,
    JobStatus.CANCELLED: EventType.JOB_CANCELLED,
    JobStatus.BLOCKED: EventType.JOB_BLOCKED,
}


class JobNotFoundError(Exception):
    pass


class WorkerNotFoundError(Exception):
    pass


class ScheduleConflictError(Exception):
    pass


class JobTransitionService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def _get_job(self, session, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise JobNotFoundError("Job not found")
        return job

    async def _apply_transition(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID, target: str, *, extra_fields: dict | None = None
    ) -> Job:
        async with self._session_factory() as session:
            job = await self._get_job(session, tenant_id, job_id)
            validate_transition(job.status, target)
            job.status = target
            for k, v in (extra_fields or {}).items():
                setattr(job, k, v)
            await session.commit()
            await session.refresh(job)

        event_type = _STATUS_TO_EVENT.get(JobStatus(target), EventType.JOB_UPDATED)
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=event_type,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id), "status": target},
        )
        return job

    async def update_job_status(self, tenant_id: uuid.UUID, job_id: uuid.UUID, target_status: str) -> Job:
        return await self._apply_transition(tenant_id, job_id, target_status)

    async def schedule(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID, start: datetime, end: datetime
    ) -> Job:
        async with self._session_factory() as session:
            job = await self._get_job(session, tenant_id, job_id)
            job.scheduled_start = start
            job.scheduled_end = end
            if job.status == JobStatus.DRAFT:
                validate_transition(job.status, JobStatus.SCHEDULED)
                job.status = JobStatus.SCHEDULED
            await session.commit()
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_SCHEDULED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id), "start": start.isoformat(), "end": end.isoformat()},
        )
        return job

    async def reschedule(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID, start: datetime, end: datetime
    ) -> Job:
        async with self._session_factory() as session:
            job = await self._get_job(session, tenant_id, job_id)
            job.scheduled_start = start
            job.scheduled_end = end
            await session.commit()
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_RESCHEDULED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id), "start": start.isoformat(), "end": end.isoformat()},
        )
        return job

    async def assign(self, tenant_id: uuid.UUID, job_id: uuid.UUID, worker_id: uuid.UUID) -> Job:
        async with self._session_factory() as session:
            job = await self._get_job(session, tenant_id, job_id)
            worker = await session.get(Worker, worker_id)
            if worker is None or worker.tenant_id != tenant_id:
                raise WorkerNotFoundError("Worker not found")
            if not worker.active or worker.status in (WorkerStatus.INACTIVE, WorkerStatus.OFFLINE):
                raise WorkerNotFoundError(f"Worker is not available (status={worker.status})")
            if job.service_type and worker.service_types and job.service_type not in worker.service_types:
                raise WorkerNotFoundError(
                    f"Worker does not support service type '{job.service_type}'"
                )

            if job.scheduled_start and job.scheduled_end:
                conflict_query = select(Job).where(
                    Job.tenant_id == tenant_id,
                    Job.id != job_id,
                    Job.assigned_user_id == worker_id,
                    Job.status.notin_([JobStatus.CANCELLED, JobStatus.CLOSED]),
                    Job.scheduled_start < job.scheduled_end,
                    Job.scheduled_end > job.scheduled_start,
                )
                conflict = (await session.execute(conflict_query)).scalars().first()
                if conflict is not None:
                    raise ScheduleConflictError(
                        f"Worker already assigned to job {conflict.job_number} at an overlapping time"
                    )

            job.assigned_user_id = worker_id
            await session.commit()
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_ASSIGNED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id), "worker_id": str(worker_id)},
        )
        return job

    async def unassign(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        async with self._session_factory() as session:
            job = await self._get_job(session, tenant_id, job_id)
            job.assigned_user_id = None
            await session.commit()
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_UNASSIGNED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id)},
        )
        return job

    async def dispatch(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        return await self._apply_transition(tenant_id, job_id, JobStatus.DISPATCHED)

    async def start(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        return await self._apply_transition(
            tenant_id, job_id, JobStatus.IN_PROGRESS, extra_fields={"actual_start": datetime.now(timezone.utc)}
        )

    async def complete(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        return await self._apply_transition(
            tenant_id, job_id, JobStatus.QA_PENDING, extra_fields={"actual_end": datetime.now(timezone.utc)}
        )

    async def cancel(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        return await self._apply_transition(tenant_id, job_id, JobStatus.CANCELLED)

    async def block(self, tenant_id: uuid.UUID, job_id: uuid.UUID, reason: str) -> Job:
        async with self._session_factory() as session:
            job = await self._get_job(session, tenant_id, job_id)
            validate_transition(job.status, JobStatus.BLOCKED)
            previous_status = job.status
            job.status = JobStatus.BLOCKED
            session.add(
                OperationsException(
                    tenant_id=tenant_id,
                    type=ExceptionType.JOB_BLOCKED,
                    severity=ExceptionSeverity.HIGH,
                    entity_type="job",
                    entity_id=job.id,
                    description=f"Job {job.job_number} blocked: {reason}",
                    recommended_action="Resolve the blocker, then unblock the job.",
                    status=ExceptionStatus.OPEN,
                )
            )
            await session.commit()
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_BLOCKED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id), "reason": reason, "previous_status": previous_status},
        )
        return job

    async def unblock(self, tenant_id: uuid.UUID, job_id: uuid.UUID, target_status: str) -> Job:
        job = await self._apply_transition(tenant_id, job_id, target_status)

        async with self._session_factory() as session:
            open_exceptions = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id,
                        OperationsException.type == ExceptionType.JOB_BLOCKED,
                        OperationsException.entity_id == job_id,
                        OperationsException.status == ExceptionStatus.OPEN,
                    )
                )
            ).scalars().all()
            for exc in open_exceptions:
                exc.status = ExceptionStatus.RESOLVED
                exc.resolved_at = datetime.now(timezone.utc)
            await session.commit()

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_UNBLOCKED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id)},
        )
        return job
