import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.finance import JobCost
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/job-costs", tags=["job-costs"])


def _to_dict(c: JobCost) -> dict[str, Any]:
    return {
        "id": str(c.id), "job_id": str(c.job_id), "category": c.category, "description": c.description,
        "quantity": str(c.quantity), "unit_cost": str(c.unit_cost), "total_cost": str(c.total_cost),
        "source": c.source,
    }


@router.get("")
async def list_job_costs(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(JobCost).where(JobCost.tenant_id == current_user.tenant_id, JobCost.job_id == job_id)
        )
    ).scalars().all()
    return {"job_costs": [_to_dict(c) for c in rows]}


@router.post("")
async def record_job_cost(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {job_id, category, description?, quantity?, unit_cost}"""
    try:
        output = await registry.execute("finance.record_job_cost", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/sync-materials")
async def sync_material_costs(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.sync_material_costs", {"job_id": str(job_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
