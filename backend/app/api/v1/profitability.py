"""section 32 / /finance/profitability: per-job profitability, computed
from the *existing* Job economics fields plus real JobCost rows — never a
new duplicate revenue/cost store."""

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.models.finance import JobCost
from app.models.operations import Job

router = APIRouter(prefix="/profitability", tags=["profitability"])


def _job_profitability(job: Job, costs: list[JobCost]) -> dict[str, Any]:
    by_category: dict[str, Decimal] = {}
    for c in costs:
        by_category[c.category] = by_category.get(c.category, Decimal("0")) + c.total_cost

    estimated_margin_pct = None
    if job.estimated_revenue and job.estimated_cost is not None:
        estimated_margin_pct = round(
            (float(job.estimated_revenue) - float(job.estimated_cost)) / float(job.estimated_revenue) * 100, 1
        )

    actual_margin_pct = None
    revenue = job.actual_revenue or job.estimated_revenue
    if revenue and job.actual_cost is not None:
        actual_margin_pct = round((float(revenue) - float(job.actual_cost)) / float(revenue) * 100, 1)

    return {
        "job_id": str(job.id),
        "job_number": job.job_number,
        "title": job.title,
        "estimated_revenue": str(job.estimated_revenue) if job.estimated_revenue is not None else None,
        "estimated_cost": str(job.estimated_cost) if job.estimated_cost is not None else None,
        "estimated_margin_pct": estimated_margin_pct,
        "actual_revenue": str(job.actual_revenue) if job.actual_revenue is not None else None,
        "actual_cost": str(job.actual_cost) if job.actual_cost is not None else None,
        "actual_margin_pct": actual_margin_pct,
        "cost_breakdown": {k: str(v) for k, v in by_category.items()},
    }


@router.get("/jobs/{job_id}")
async def get_job_profitability(
    job_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    job = await db.get(Job, job_id)
    if job is None or job.tenant_id != current_user.tenant_id:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    costs = (
        await db.execute(select(JobCost).where(JobCost.tenant_id == current_user.tenant_id, JobCost.job_id == job_id))
    ).scalars().all()
    return _job_profitability(job, list(costs))


@router.get("")
async def list_profitability(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    jobs = (
        await db.execute(
            select(Job).where(Job.tenant_id == current_user.tenant_id, Job.actual_cost.is_not(None))
        )
    ).scalars().all()
    results = []
    for job in jobs:
        costs = (
            await db.execute(
                select(JobCost).where(JobCost.tenant_id == current_user.tenant_id, JobCost.job_id == job.id)
            )
        ).scalars().all()
        results.append(_job_profitability(job, list(costs)))
    return {"jobs": results}
