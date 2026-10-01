"""section 34: AI job summary — built from real data only, concise
rationale, no chain-of-thought, explicit about missing information."""

import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.crm import Customer
from app.models.operations import (
    ExceptionStatus,
    Job,
    JobAttachment,
    JobMaterial,
    JobQA,
    JobTask,
    OperationsException,
    TaskStatus,
)
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


class GenerateJobSummaryInput(BaseModel):
    job_id: uuid.UUID


class GenerateJobSummaryOutput(BaseModel):
    job_id: str
    summary: str


class GenerateJobSummary(Tool):
    name = "operations.generate_job_summary"
    description = "Summarize a job's real current state — status, customer, schedule, worker, tasks, materials, exceptions, QA."
    input_schema = GenerateJobSummaryInput
    output_schema = GenerateJobSummaryOutput
    required_permission = Permission.READ_JOBS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GenerateJobSummaryInput, context: ExecutionContext) -> GenerateJobSummaryOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            job = await session.get(Job, input.job_id)
            if job is None or job.tenant_id != context.tenant_id:
                raise ValueError("Job not found")

            customer = await session.get(Customer, job.customer_id)

            tasks = (
                await session.execute(
                    select(JobTask).where(JobTask.tenant_id == context.tenant_id, JobTask.job_id == job.id)
                )
            ).scalars().all()
            materials = (
                await session.execute(
                    select(JobMaterial).where(
                        JobMaterial.tenant_id == context.tenant_id, JobMaterial.job_id == job.id
                    )
                )
            ).scalars().all()
            open_exceptions = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == context.tenant_id,
                        OperationsException.entity_id == job.id,
                        OperationsException.status == ExceptionStatus.OPEN,
                    )
                )
            ).scalars().all()
            qa = (
                await session.execute(
                    select(JobQA).where(JobQA.tenant_id == context.tenant_id, JobQA.job_id == job.id)
                )
            ).scalar_one_or_none()
            attachment_count = len(
                (
                    await session.execute(
                        select(JobAttachment).where(
                            JobAttachment.tenant_id == context.tenant_id, JobAttachment.job_id == job.id
                        )
                    )
                ).scalars().all()
            )

        lines = [f"Job {job.job_number} ('{job.title}') — status {job.status}, priority {job.priority}."]
        lines.append(f"Customer: {customer.name if customer else 'unknown — not found'}.")
        if job.scheduled_start:
            lines.append(f"Scheduled {job.scheduled_start.isoformat()} - {job.scheduled_end.isoformat() if job.scheduled_end else '?'}.")
        else:
            lines.append("Not yet scheduled.")
        lines.append(f"Assigned worker: {job.assigned_user_id or 'unassigned'}.")

        required_incomplete = [t for t in tasks if t.required and t.status not in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)]
        if tasks:
            lines.append(
                f"{len(tasks)} task(s), {len(required_incomplete)} required task(s) still incomplete."
            )
        else:
            lines.append("No tasks recorded.")

        lines.append(f"{len(materials)} material(s) recorded. {attachment_count} attachment(s) uploaded.")
        lines.append(f"QA status: {qa.status if qa else 'NOT_STARTED'}.")

        if open_exceptions:
            lines.append(
                f"{len(open_exceptions)} open exception(s): "
                + "; ".join(f"{e.type} ({e.severity})" for e in open_exceptions)
                + "."
            )
        else:
            lines.append("No open exceptions.")

        return GenerateJobSummaryOutput(job_id=str(job.id), summary=" ".join(lines))
