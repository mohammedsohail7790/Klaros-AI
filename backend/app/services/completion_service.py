"""section 26/28: completion packet + close-out.

The packet is assembled entirely from real rows — job fields, tasks,
attachments, materials, QA result, notes. Nothing here invents a value for
a field that has no data; it's simply omitted/null.

Close-out is deliberately strict (section 28): COMPLETED status (which the
job state machine already only reaches via a QA pass — see qa_service.py),
all required tasks complete, and a READY completion packet. `close_job`
re-checks all three itself rather than trusting the caller.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.operations import (
    CompletionPacket,
    Job,
    JobAttachment,
    JobMaterial,
    JobQA,
    JobStatus,
    JobTask,
    PacketStatus,
    QAStatus,
    TaskStatus,
)
from app.services.job_state_machine import validate_transition


class JobNotFoundError(Exception):
    pass


class CloseOutNotReadyError(Exception):
    def __init__(self, failures: list[str]) -> None:
        super().__init__("; ".join(failures))
        self.failures = failures


class CompletionService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def generate_completion_packet(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> CompletionPacket:
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            tasks = (
                await session.execute(
                    select(JobTask).where(JobTask.tenant_id == tenant_id, JobTask.job_id == job_id)
                )
            ).scalars().all()
            attachments = (
                await session.execute(
                    select(JobAttachment).where(
                        JobAttachment.tenant_id == tenant_id, JobAttachment.job_id == job_id
                    )
                )
            ).scalars().all()
            materials = (
                await session.execute(
                    select(JobMaterial).where(JobMaterial.tenant_id == tenant_id, JobMaterial.job_id == job_id)
                )
            ).scalars().all()
            qa = (
                await session.execute(
                    select(JobQA).where(JobQA.tenant_id == tenant_id, JobQA.job_id == job_id)
                )
            ).scalar_one_or_none()

            summary = {
                "job_number": job.job_number,
                "title": job.title,
                "status": job.status,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "tasks": [{"title": t.title, "status": t.status} for t in tasks],
                "attachment_count": len(attachments),
                "attachments": [{"kind": a.kind, "filename": a.filename} for a in attachments],
                "materials": [{"name": m.name, "quantity": float(m.quantity)} for m in materials],
                "qa_status": qa.status if qa else QAStatus.NOT_STARTED,
                "customer_notes": job.customer_notes,
                "internal_notes": job.internal_notes,
            }

            packet = (
                await session.execute(
                    select(CompletionPacket).where(
                        CompletionPacket.tenant_id == tenant_id, CompletionPacket.job_id == job_id
                    )
                )
            ).scalar_one_or_none()
            if packet is None:
                packet = CompletionPacket(tenant_id=tenant_id, job_id=job_id)
                session.add(packet)

            packet.summary = summary
            packet.status = (
                PacketStatus.READY
                if job.status in (JobStatus.COMPLETED, JobStatus.CLOSED) and (qa and qa.status == QAStatus.PASSED)
                else PacketStatus.DRAFT
            )
            await session.commit()
            await session.refresh(packet)
            return packet

    async def close_job(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            validate_transition(job.status, JobStatus.CLOSED)

            failures: list[str] = []

            qa = (
                await session.execute(
                    select(JobQA).where(JobQA.tenant_id == tenant_id, JobQA.job_id == job_id)
                )
            ).scalar_one_or_none()
            if qa is None or qa.status != QAStatus.PASSED:
                failures.append("QA has not passed")

            incomplete_required = (
                await session.execute(
                    select(JobTask).where(
                        JobTask.tenant_id == tenant_id,
                        JobTask.job_id == job_id,
                        JobTask.required.is_(True),
                        JobTask.status.notin_([TaskStatus.COMPLETED, TaskStatus.SKIPPED]),
                    )
                )
            ).scalars().all()
            if incomplete_required:
                failures.append(f"{len(incomplete_required)} required task(s) incomplete")

            packet = (
                await session.execute(
                    select(CompletionPacket).where(
                        CompletionPacket.tenant_id == tenant_id, CompletionPacket.job_id == job_id
                    )
                )
            ).scalar_one_or_none()
            if packet is None or packet.status != PacketStatus.READY:
                failures.append("completion packet is not READY — call generate_completion_packet first")

            if failures:
                raise CloseOutNotReadyError(failures)

            job.status = JobStatus.CLOSED
            await session.commit()
            await session.refresh(job)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.JOB_CLOSED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={"job_id": str(job.id)},
        )
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.INVOICE_TRIGGER_REQUESTED,
            source="operations",
            entity_type="job",
            entity_id=job.id,
            payload={
                "job_id": str(job.id),
                "customer_id": str(job.customer_id),
                "note": "Finance module not implemented yet (Phase 5) — no invoice was created.",
            },
        )
        return job
