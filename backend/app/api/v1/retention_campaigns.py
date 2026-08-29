import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.retention import RetentionCampaign
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/retention/campaigns", tags=["retention-campaigns"])


@router.get("")
async def list_campaigns(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(RetentionCampaign).where(RetentionCampaign.tenant_id == current_user.tenant_id))).scalars().all()
    return {"campaigns": [{"id": str(c.id), "name": c.name, "type": c.type, "status": c.status} for c in rows]}


@router.post("")
async def create_campaign(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {name, type}"""
    try:
        output = await registry.execute("retention.create_campaign", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{campaign_id}/status")
async def set_status(campaign_id: uuid.UUID, status: str, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.set_campaign_status", {"campaign_id": str(campaign_id), "status": status}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{campaign_id}/enroll")
async def enroll_customer(campaign_id: uuid.UUID, customer_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "retention.enroll_customer_in_campaign", {"campaign_id": str(campaign_id), "customer_id": str(customer_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/activities/execute-due")
async def execute_due(current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.execute_due_activities", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
