import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.marketing import ReactivationCampaign, ReactivationCandidate
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/reactivation", tags=["marketing-reactivation"])


@router.get("/campaigns")
async def list_campaigns(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(ReactivationCampaign).where(ReactivationCampaign.tenant_id == current_user.tenant_id))).scalars().all()
    return {"campaigns": [{"id": str(c.id), "name": c.name, "status": c.status} for c in rows]}


@router.post("/campaigns")
async def create_campaign(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_reactivation_campaign", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/candidates")
async def list_candidates(campaign_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    query = select(ReactivationCandidate).where(ReactivationCandidate.tenant_id == current_user.tenant_id)
    if campaign_id:
        query = query.where(ReactivationCandidate.campaign_id == campaign_id)
    rows = (await db.execute(query)).scalars().all()
    return {
        "candidates": [
            {
                "id": str(c.id), "campaign_id": str(c.campaign_id), "customer_id": str(c.customer_id) if c.customer_id else None,
                "lead_id": str(c.lead_id) if c.lead_id else None, "reason": c.reason, "score": c.score, "status": c.status,
            }
            for c in rows
        ]
    }


@router.post("/campaigns/{campaign_id}/identify-inactive-customers")
async def identify_inactive_customers(campaign_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.identify_inactive_customers", {"campaign_id": str(campaign_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/campaigns/{campaign_id}/identify-unbooked-qualified-leads")
async def identify_unbooked_qualified_leads(campaign_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.identify_unbooked_qualified_leads", {"campaign_id": str(campaign_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
