"""section 23: deterministic delay detection — no LLM.

Three checks, all pure timestamp comparisons against `datetime.now(UTC)`:
- JOB_DELAYED: still IN_PROGRESS after its scheduled_end has passed.
- JOB_OVERDUE: still SCHEDULED (never dispatched) OVERDUE_TOLERANCE_MINUTES
  after its scheduled_start.
- JOB_UNASSIGNED: SCHEDULED with no assigned_user_id.

AI-driven diagnosis/recommendation (section 33) can layer on top of these
facts later; it must never replace this deterministic layer.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.operations import ExceptionSeverity, ExceptionType, Job, JobStatus
from app.services.exception_service import ExceptionService

OVERDUE_TOLERANCE_MINUTES = 30


class DelayDetectionService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus, exceptions: ExceptionService) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._exceptions = exceptions

    async def run(self, tenant_id: uuid.UUID) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        created = {"JOB_DELAYED": 0, "JOB_OVERDUE": 0, "JOB_UNASSIGNED": 0}

        async with self._session_factory() as session:
            jobs = (
                await session.execute(
                    select(Job).where(
                        Job.tenant_id == tenant_id,
                        Job.status.notin_([JobStatus.CLOSED, JobStatus.CANCELLED, JobStatus.COMPLETED]),
                    )
                )
            ).scalars().all()

        for job in jobs:
            scheduled_end = _naive_utc(job.scheduled_end) if job.scheduled_end else None
            scheduled_start = _naive_utc(job.scheduled_start) if job.scheduled_start else None
            now_naive = now.replace(tzinfo=None)

            if job.status == JobStatus.IN_PROGRESS and scheduled_end and now_naive > scheduled_end:
                _, deduped = await self._exceptions.create_exception(
                    tenant_id,
                    type=ExceptionType.JOB_DELAYED,
                    severity=ExceptionSeverity.HIGH,
                    entity_type="job",
                    entity_id=job.id,
                    description=f"Job {job.job_number} is still IN_PROGRESS past its scheduled end.",
                    recommended_action="Check in with the assigned worker or reschedule the remaining work.",
                )
                if not deduped:
                    created["JOB_DELAYED"] += 1
                    await self._bus.publish(
                        tenant_id=tenant_id,
                        event_type=EventType.JOB_DELAYED,
                        source="operations",
                        entity_type="job",
                        entity_id=job.id,
                        payload={"job_id": str(job.id)},
                    )

            if (
                job.status == JobStatus.SCHEDULED
                and scheduled_start
                and now_naive > scheduled_start + timedelta(minutes=OVERDUE_TOLERANCE_MINUTES)
            ):
                _, deduped = await self._exceptions.create_exception(
                    tenant_id,
                    type=ExceptionType.JOB_OVERDUE,
                    severity=ExceptionSeverity.HIGH,
                    entity_type="job",
                    entity_id=job.id,
                    description=f"Job {job.job_number} was never dispatched and is now overdue.",
                    recommended_action="Dispatch immediately or reschedule.",
                )
                if not deduped:
                    created["JOB_OVERDUE"] += 1

            if job.status == JobStatus.SCHEDULED and job.assigned_user_id is None:
                _, deduped = await self._exceptions.create_exception(
                    tenant_id,
                    type=ExceptionType.JOB_UNASSIGNED,
                    severity=(
                        ExceptionSeverity.CRITICAL
                        if job.priority in ("URGENT", "CRITICAL")
                        else ExceptionSeverity.MEDIUM
                    ),
                    entity_type="job",
                    entity_id=job.id,
                    description=f"Job {job.job_number} is scheduled but has no technician assigned.",
                    recommended_action="Assign a worker before the scheduled start.",
                )
                if not deduped:
                    created["JOB_UNASSIGNED"] += 1

        return created


def _naive_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
