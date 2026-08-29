from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.marketing import NurtureEnrollment, NurtureSequence
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/nurture", tags=["marketing-nurture"])


@router.get("/sequences")
async def list_sequences(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(NurtureSequence).where(NurtureSequence.tenant_id == current_user.tenant_id))).scalars().all()
    return {"sequences": [{"id": str(s.id), "name": s.name, "trigger_type": s.trigger_type, "status": s.status} for s in rows]}


@router.post("/sequences")
async def create_sequence(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_nurture_sequence", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/candidates/stale-leads")
async def find_stale_lead_candidates(current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.find_stale_lead_candidates", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/enrollments")
async def enroll_lead(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {sequence_id, lead_id}"""
    try:
        output = await registry.execute("marketing.enroll_lead_in_nurture", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/enrollments")
async def list_enrollments(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(NurtureEnrollment).where(NurtureEnrollment.tenant_id == current_user.tenant_id))).scalars().all()
    return {"enrollments": [{"id": str(e.id), "sequence_id": str(e.sequence_id), "lead_id": str(e.lead_id), "status": e.status} for e in rows]}


@router.post("/activities/execute-due")
async def execute_due(current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.execute_due_nurture_activities", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
