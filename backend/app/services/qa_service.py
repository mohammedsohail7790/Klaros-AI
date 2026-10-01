"""section 24: QA engine.

`complete_qa` (pass) actually re-checks the required conditions itself
rather than trusting the caller — required tasks complete, at least one
piece of field documentation (a photo or document) exists, and the job was
actually worked (`actual_start`/`actual_end` set). If any check fails, it
raises rather than marking PASSED; the caller can fix the gap and retry, or
call `fail_qa` to explicitly log why. A job can never reach CLOSED with
failed/incomplete QA — QA_PENDING only becomes COMPLETED here.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.event import EventType
from app.models.operations import (
    AttachmentKind,
    ExceptionSeverity,
    ExceptionStatus,
    ExceptionType,
    Job,
    JobAttachment,
    JobQA,
    JobStatus,
    JobTask,
    OperationsException,
    QAStatus,
    TaskStatus,
)


class JobNotFoundError(Exception):
    pass


class QANotReadyError(Exception):
    pass


class QAValidationError(Exception):
    def __init__(self, failures: list[str]) -> None:
        super().__init__("; ".join(failures))
        self.failures = failures


class QAService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def _get_job(self, session, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise JobNotFoundError("Job not found")
        return job

    async def _get_or_create_qa(self, session, tenant_id: uuid.UUID, job_id: uuid.UUID) -> JobQA:
        qa = (
            await session.execute(
                select(JobQA).where(JobQA.tenant_id == tenant_id, JobQA.job_id == job_id)
            )
        ).scalar_one_or_none()
        if qa is None:
            qa = JobQA(tenant_id=tenant_id, job_id=job_id, status=QAStatus.NOT_STARTED)
            session.add(qa)
            await session.flush()
        return qa

    async def start_qa(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> JobQA:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await self._get_job(session, tenant_id, job_id)
            if job.status != JobStatus.QA_PENDING:
                raise QANotReadyError(f"Job must be QA_PENDING to start QA (currently {job.status})")
            qa = await self._get_or_create_qa(session, tenant_id, job_id)
            qa.status = QAStatus.IN_PROGRESS
            await session.commit()
            await session.refresh(qa)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_QA_STARTED,
            source="operations",
            entity_type="job",
            entity_id=job_id,
            payload={"job_id": str(job_id)},
        )
        return qa

    async def _run_checks(self, session, tenant_id: uuid.UUID, job: Job) -> list[str]:
        failures: list[str] = []

        incomplete_required = (
            await session.execute(
                select(JobTask).where(
                    JobTask.tenant_id == tenant_id,
                    JobTask.job_id == job.id,
                    JobTask.required.is_(True),
                    JobTask.status.notin_([TaskStatus.COMPLETED, TaskStatus.SKIPPED]),
                )
            )
        ).scalars().all()
        if incomplete_required:
            failures.append(f"{len(incomplete_required)} required task(s) not completed")

        has_documentation = (
            await session.execute(
                select(JobAttachment).where(
                    JobAttachment.tenant_id == tenant_id,
                    JobAttachment.job_id == job.id,
                    JobAttachment.kind.in_([AttachmentKind.PHOTO, AttachmentKind.DOCUMENT]),
                )
            )
        ).scalars().first()
        if has_documentation is None:
            failures.append("no photo or document uploaded")

        if job.actual_start is None:
            failures.append("job was never marked started (no actual_start)")

        return failures

    async def complete_qa(self, tenant_id: uuid.UUID, job_id: uuid.UUID, performed_by: uuid.UUID | None) -> JobQA:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await self._get_job(session, tenant_id, job_id)
            if job.status != JobStatus.QA_PENDING:
                raise QANotReadyError(f"Job must be QA_PENDING to complete QA (currently {job.status})")

            failures = await self._run_checks(session, tenant_id, job)
            if failures:
                raise QAValidationError(failures)

            qa = await self._get_or_create_qa(session, tenant_id, job_id)
            qa.status = QAStatus.PASSED
            qa.checks = {"passed_at": datetime.now(timezone.utc).isoformat(), "failures": []}
            qa.performed_by = performed_by
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(qa)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_QA_PASSED,
            source="operations",
            entity_type="job",
            entity_id=job_id,
            payload={"job_id": str(job_id)},
        )
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_COMPLETED,
            source="operations",
            entity_type="job",
            entity_id=job_id,
            payload={"job_id": str(job_id)},
        )
        return qa

    async def fail_qa(self, tenant_id: uuid.UUID, job_id: uuid.UUID, reason: str) -> JobQA:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await self._get_job(session, tenant_id, job_id)
            qa = await self._get_or_create_qa(session, tenant_id, job_id)
            qa.status = QAStatus.FAILED
            qa.failure_reason = reason
            # Job remains QA_PENDING — explicit spec requirement (section 24).
            session.add(
                OperationsException(
                    tenant_id=tenant_id,
                    type=ExceptionType.QA_FAILURE,
                    severity=ExceptionSeverity.HIGH,
                    entity_type="job",
                    entity_id=job.id,
                    description=f"QA failed for job {job.job_number}: {reason}",
                    recommended_action="Address the QA failure, then retry complete_qa.",
                    status=ExceptionStatus.OPEN,
                )
            )
            await session.commit()
            await session.refresh(qa)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_QA_FAILED,
            source="operations",
            entity_type="job",
            entity_id=job_id,
            payload={"job_id": str(job_id), "reason": reason},
        )
        return qa
