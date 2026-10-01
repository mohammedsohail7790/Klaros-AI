"""section 5/30: job CRUD, assignment, scheduling, dispatch, and the
explicit lead->customer->appointment->job conversion tool (section 4)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.calendar.base import DoubleBookingError
from app.db.session import set_tenant_context
from app.models.operations import Job, JobPriority
from app.models.rbac import Permission
from app.services.conversion_service import LeadConversionService
from app.services.job_service import CreateJobInput, JobService
from app.services.job_state_machine import InvalidJobTransitionError
from app.services.job_transition_service import (
    JobTransitionService,
    ScheduleConflictError,
    WorkerNotFoundError,
)
from app.tools.base import ExecutionContext, Tool


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "customer_id": str(job.customer_id),
        "lead_id": str(job.lead_id) if job.lead_id else None,
        "appointment_id": str(job.appointment_id) if job.appointment_id else None,
        "job_number": job.job_number,
        "title": job.title,
        "description": job.description,
        "service_type": job.service_type,
        "status": job.status,
        "priority": job.priority,
        "location": job.location,
        "scheduled_start": job.scheduled_start.isoformat() if job.scheduled_start else None,
        "scheduled_end": job.scheduled_end.isoformat() if job.scheduled_end else None,
        "assigned_user_id": str(job.assigned_user_id) if job.assigned_user_id else None,
        "estimated_duration_minutes": job.estimated_duration_minutes,
        "actual_start": job.actual_start.isoformat() if job.actual_start else None,
        "actual_end": job.actual_end.isoformat() if job.actual_end else None,
        "estimated_revenue": float(job.estimated_revenue) if job.estimated_revenue is not None else None,
        "estimated_cost": float(job.estimated_cost) if job.estimated_cost is not None else None,
        "estimated_margin": float(job.estimated_margin) if job.estimated_margin is not None else None,
        "customer_notes": job.customer_notes,
        "internal_notes": job.internal_notes,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat(),
    }


class JobOutput(BaseModel):
    job: dict[str, Any]
    deduplicated: bool = False


class CreateJobInputSchema(BaseModel):
    title: str
    customer_id: uuid.UUID
    lead_id: uuid.UUID | None = None
    appointment_id: uuid.UUID | None = None
    description: str | None = None
    service_type: str | None = None
    priority: str = "NORMAL"
    location: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_duration_minutes: int | None = None
    estimated_revenue: float | None = None
    estimated_cost: float | None = None
    idempotency_key: str | None = None


class CreateJob(Tool):
    name = "operations.create_job"
    description = "Create a job, optionally from an existing appointment. Idempotent."
    input_schema = CreateJobInputSchema
    output_schema = JobOutput
    required_permission = Permission.CREATE_JOB

    def __init__(self, job_service: JobService) -> None:
        self._job_service = job_service

    async def execute(self, input: CreateJobInputSchema, context: ExecutionContext) -> JobOutput:
        if input.appointment_id and not input.idempotency_key:
            job, deduped = await self._job_service.create_job_from_appointment(
                context.tenant_id, input.appointment_id
            )
            return JobOutput(job=_job_to_dict(job), deduplicated=deduped)

        job, deduped = await self._job_service.create_job(
            context.tenant_id,
            CreateJobInput(
                title=input.title,
                customer_id=input.customer_id,
                lead_id=input.lead_id,
                appointment_id=input.appointment_id,
                description=input.description,
                service_type=input.service_type,
                priority=input.priority,
                location=input.location,
                scheduled_start=input.scheduled_start,
                scheduled_end=input.scheduled_end,
                estimated_duration_minutes=input.estimated_duration_minutes,
                estimated_revenue=input.estimated_revenue,
                estimated_cost=input.estimated_cost,
                idempotency_key=input.idempotency_key,
            ),
        )
        return JobOutput(job=_job_to_dict(job), deduplicated=deduped)


class GetJobInput(BaseModel):
    job_id: uuid.UUID


class GetJob(Tool):
    name = "operations.get_job"
    description = "Fetch a job by id, scoped to the caller's tenant."
    input_schema = GetJobInput
    output_schema = JobOutput
    required_permission = Permission.READ_JOBS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetJobInput, context: ExecutionContext) -> JobOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            job = await session.get(Job, input.job_id)
            if job is None or job.tenant_id != context.tenant_id:
                raise ValueError("Job not found")
            return JobOutput(job=_job_to_dict(job))


class UpdateJobInput(BaseModel):
    job_id: uuid.UUID
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    customer_notes: str | None = None
    internal_notes: str | None = None


class UpdateJob(Tool):
    name = "operations.update_job"
    description = "Update mutable, non-state-machine fields on a job."
    input_schema = UpdateJobInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: UpdateJobInput, context: ExecutionContext) -> JobOutput:
        if input.priority is not None:
            try:
                JobPriority(input.priority)
            except ValueError:
                raise ValueError(f"Invalid job priority: {input.priority}") from None

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            job = await session.get(Job, input.job_id)
            if job is None or job.tenant_id != context.tenant_id:
                raise ValueError("Job not found")
            for field in ("title", "description", "priority", "customer_notes", "internal_notes"):
                value = getattr(input, field)
                if value is not None:
                    setattr(job, field, value)
            await session.commit()
            await session.refresh(job)
            return JobOutput(job=_job_to_dict(job))


class SearchJobsInput(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_user_id: uuid.UUID | None = None
    q: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchJobsOutput(BaseModel):
    jobs: list[dict[str, Any]]
    total: int


class SearchJobs(Tool):
    name = "operations.search_jobs"
    description = "Search/filter/paginate jobs for this tenant."
    input_schema = SearchJobsInput
    output_schema = SearchJobsOutput
    required_permission = Permission.READ_JOBS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: SearchJobsInput, context: ExecutionContext) -> SearchJobsOutput:
        from sqlalchemy import func

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            query = select(Job).where(Job.tenant_id == context.tenant_id)
            count_query = select(func.count(Job.id)).where(Job.tenant_id == context.tenant_id)

            if input.status:
                query = query.where(Job.status == input.status)
                count_query = count_query.where(Job.status == input.status)
            if input.priority:
                query = query.where(Job.priority == input.priority)
                count_query = count_query.where(Job.priority == input.priority)
            if input.assigned_user_id:
                query = query.where(Job.assigned_user_id == input.assigned_user_id)
                count_query = count_query.where(Job.assigned_user_id == input.assigned_user_id)
            if input.q:
                like = f"%{input.q}%"
                cond = or_(Job.title.ilike(like), Job.job_number.ilike(like), Job.service_type.ilike(like))
                query = query.where(cond)
                count_query = count_query.where(cond)

            total = (await session.execute(count_query)).scalar_one()
            query = query.order_by(Job.created_at.desc()).limit(input.limit).offset(input.offset)
            jobs = (await session.execute(query)).scalars().all()
            return SearchJobsOutput(jobs=[_job_to_dict(j) for j in jobs], total=total)


class AssignJobInput(BaseModel):
    job_id: uuid.UUID
    worker_id: uuid.UUID


class AssignJob(Tool):
    name = "operations.assign_job"
    description = "Assign a job to a worker, checking availability/skill/schedule conflicts."
    input_schema = AssignJobInput
    output_schema = JobOutput
    required_permission = Permission.ASSIGN_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: AssignJobInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.assign(context.tenant_id, input.job_id, input.worker_id)
        except (WorkerNotFoundError, ScheduleConflictError) as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class UnassignJobInput(BaseModel):
    job_id: uuid.UUID


class UnassignJob(Tool):
    name = "operations.unassign_job"
    description = "Remove a job's worker assignment."
    input_schema = UnassignJobInput
    output_schema = JobOutput
    required_permission = Permission.ASSIGN_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: UnassignJobInput, context: ExecutionContext) -> JobOutput:
        job = await self._transitions.unassign(context.tenant_id, input.job_id)
        return JobOutput(job=_job_to_dict(job))


class ScheduleJobInput(BaseModel):
    job_id: uuid.UUID
    start_time: datetime
    end_time: datetime


class ScheduleJob(Tool):
    name = "operations.schedule_job"
    description = "Set a job's schedule (DRAFT -> SCHEDULED)."
    input_schema = ScheduleJobInput
    output_schema = JobOutput
    required_permission = Permission.SCHEDULE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: ScheduleJobInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.schedule(context.tenant_id, input.job_id, input.start_time, input.end_time)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class RescheduleJobInput(BaseModel):
    job_id: uuid.UUID
    start_time: datetime
    end_time: datetime


class RescheduleJob(Tool):
    name = "operations.reschedule_job"
    description = "Change a job's scheduled time without changing its status."
    input_schema = RescheduleJobInput
    output_schema = JobOutput
    required_permission = Permission.SCHEDULE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: RescheduleJobInput, context: ExecutionContext) -> JobOutput:
        job = await self._transitions.reschedule(context.tenant_id, input.job_id, input.start_time, input.end_time)
        return JobOutput(job=_job_to_dict(job))


class JobIdInput(BaseModel):
    job_id: uuid.UUID


class DispatchJob(Tool):
    name = "operations.dispatch_job"
    description = "Transition a job SCHEDULED -> DISPATCHED."
    input_schema = JobIdInput
    output_schema = JobOutput
    required_permission = Permission.DISPATCH_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.dispatch(context.tenant_id, input.job_id)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class UpdateJobStatusInput(BaseModel):
    job_id: uuid.UUID
    target_status: str


class UpdateJobStatus(Tool):
    """Generic transition used for steps the spec's tool list doesn't name
    explicitly (EN_ROUTE, ON_SITE) — see job_transition_service.py's
    module docstring for why."""

    name = "operations.update_job_status"
    description = "Transition a job to any status the state machine allows from its current one."
    input_schema = UpdateJobStatusInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: UpdateJobStatusInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.update_job_status(context.tenant_id, input.job_id, input.target_status)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class StartJob(Tool):
    name = "operations.start_job"
    description = "Transition a job ON_SITE -> IN_PROGRESS and set actual_start."
    input_schema = JobIdInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.start(context.tenant_id, input.job_id)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class CompleteJob(Tool):
    name = "operations.complete_job"
    description = "Transition a job IN_PROGRESS -> QA_PENDING and set actual_end (field work done, awaiting QA)."
    input_schema = JobIdInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.complete(context.tenant_id, input.job_id)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class CancelJob(Tool):
    name = "operations.cancel_job"
    description = "Cancel a job (terminal state)."
    input_schema = JobIdInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.cancel(context.tenant_id, input.job_id)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class BlockJobInput(BaseModel):
    job_id: uuid.UUID
    reason: str


class BlockJob(Tool):
    name = "operations.block_job"
    description = "Block a job and create a JOB_BLOCKED exception."
    input_schema = BlockJobInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: BlockJobInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.block(context.tenant_id, input.job_id, input.reason)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class UnblockJobInput(BaseModel):
    job_id: uuid.UUID
    target_status: str = "IN_PROGRESS"


class UnblockJob(Tool):
    name = "operations.unblock_job"
    description = "Unblock a job back into an operational status and resolve its JOB_BLOCKED exception(s)."
    input_schema = UnblockJobInput
    output_schema = JobOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, transitions: JobTransitionService) -> None:
        self._transitions = transitions

    async def execute(self, input: UnblockJobInput, context: ExecutionContext) -> JobOutput:
        try:
            job = await self._transitions.unblock(context.tenant_id, input.job_id, input.target_status)
        except InvalidJobTransitionError as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class GetJobTimelineInput(BaseModel):
    job_id: uuid.UUID


class TimelineEntry(BaseModel):
    type: str
    timestamp: str
    summary: str


class GetJobTimelineOutput(BaseModel):
    job_id: str
    entries: list[TimelineEntry]


class GetJobTimeline(Tool):
    """section 14: built entirely from real audit_log rows for this job —
    never fabricated."""

    name = "operations.get_job_timeline"
    description = "Build a chronological timeline of everything on record for a job."
    input_schema = GetJobTimelineInput
    output_schema = GetJobTimelineOutput
    required_permission = Permission.READ_JOBS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetJobTimelineInput, context: ExecutionContext) -> GetJobTimelineOutput:
        from app.models.audit_log import AuditLog

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            job = await session.get(Job, input.job_id)
            if job is None or job.tenant_id != context.tenant_id:
                raise ValueError("Job not found")

            audit_rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == context.tenant_id,
                        AuditLog.entity_type == "job",
                        AuditLog.entity_id == input.job_id,
                    )
                )
            ).scalars().all()

        entries = [
            TimelineEntry(type="audit", timestamp=r.created_at.isoformat(), summary=r.action)
            for r in audit_rows
        ]
        entries.sort(key=lambda e: e.timestamp)
        return GetJobTimelineOutput(job_id=str(input.job_id), entries=entries)


class ConvertLeadAndBookInput(BaseModel):
    lead_id: uuid.UUID
    title: str
    start_time: datetime
    end_time: datetime
    assigned_user_id: uuid.UUID | None = None
    idempotency_key: str | None = None


class ConvertLeadAndBookOutput(BaseModel):
    lead_id: str
    customer_id: str
    customer_created: bool
    appointment_id: str
    job: dict[str, Any]
    deduplicated: bool


class ConvertLeadAndBook(Tool):
    """section 4: the explicit lead -> customer -> appointment -> job
    conversion, as one idempotent action."""

    name = "operations.convert_lead_and_book"
    description = "Convert a qualified lead into a customer (matched or created), book an appointment, and create the job."
    input_schema = ConvertLeadAndBookInput
    output_schema = ConvertLeadAndBookOutput
    required_permission = Permission.CREATE_JOB

    def __init__(self, conversion_service: LeadConversionService) -> None:
        self._conversion_service = conversion_service

    async def execute(
        self, input: ConvertLeadAndBookInput, context: ExecutionContext
    ) -> ConvertLeadAndBookOutput:
        try:
            result = await self._conversion_service.convert_and_book(
                context.tenant_id,
                input.lead_id,
                title=input.title,
                start_time=input.start_time,
                end_time=input.end_time,
                assigned_user_id=input.assigned_user_id,
                idempotency_key=input.idempotency_key,
            )
        except DoubleBookingError as exc:
            raise ValueError(str(exc)) from exc

        return ConvertLeadAndBookOutput(
            lead_id=str(result.lead.id),
            customer_id=str(result.customer.id),
            customer_created=result.customer_created,
            appointment_id=str(result.appointment_id),
            job=_job_to_dict(result.job),
            deduplicated=result.deduplicated,
        )
