"""section 12/29: the Operations command center — real backend data only."""

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker, set_tenant_context
from app.models.operations import ExceptionStatus, Job, JobStatus, OperationsException

router = APIRouter(prefix="/operations", tags=["operations"])


class OperationsDashboard(BaseModel):
    jobs_today: int
    unassigned_jobs: int
    at_risk_jobs: int
    blocked_jobs: int
    in_progress_jobs: int
    qa_pending_jobs: int
    completed_today: int
    open_exceptions: int


@router.get("/dashboard", response_model=OperationsDashboard)
async def get_operations_dashboard(current_user: CurrentUser = Depends(get_current_user)) -> OperationsDashboard:
    tenant_id = current_user.tenant_id
    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        jobs_today = (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.tenant_id == tenant_id,
                    Job.scheduled_start >= day_start,
                    Job.scheduled_start < day_end,
                )
            )
        ).scalar_one()

        unassigned_jobs = (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.tenant_id == tenant_id,
                    Job.status == JobStatus.SCHEDULED,
                    Job.assigned_user_id.is_(None),
                )
            )
        ).scalar_one()

        blocked_jobs = (
            await session.execute(
                select(func.count(Job.id)).where(Job.tenant_id == tenant_id, Job.status == JobStatus.BLOCKED)
            )
        ).scalar_one()

        in_progress_jobs = (
            await session.execute(
                select(func.count(Job.id)).where(Job.tenant_id == tenant_id, Job.status == JobStatus.IN_PROGRESS)
            )
        ).scalar_one()

        qa_pending_jobs = (
            await session.execute(
                select(func.count(Job.id)).where(Job.tenant_id == tenant_id, Job.status == JobStatus.QA_PENDING)
            )
        ).scalar_one()

        completed_today = (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.tenant_id == tenant_id,
                    Job.completed_at >= day_start,
                    Job.completed_at < day_end,
                )
            )
        ).scalar_one()

        open_exceptions = (
            await session.execute(
                select(func.count(OperationsException.id)).where(
                    OperationsException.tenant_id == tenant_id,
                    OperationsException.status == ExceptionStatus.OPEN,
                )
            )
        ).scalar_one()

        # "At risk": a job with any open exception attached, restricted to
        # jobs (not leads/customers) so this number matches "needs attention
        # today", not the tenant's entire exception backlog.
        at_risk_jobs = (
            await session.execute(
                select(func.count(func.distinct(OperationsException.entity_id))).where(
                    OperationsException.tenant_id == tenant_id,
                    OperationsException.status == ExceptionStatus.OPEN,
                    OperationsException.entity_type == "job",
                )
            )
        ).scalar_one()

    return OperationsDashboard(
        jobs_today=jobs_today,
        unassigned_jobs=unassigned_jobs,
        at_risk_jobs=at_risk_jobs,
        blocked_jobs=blocked_jobs,
        in_progress_jobs=in_progress_jobs,
        qa_pending_jobs=qa_pending_jobs,
        completed_today=completed_today,
        open_exceptions=open_exceptions,
    )
