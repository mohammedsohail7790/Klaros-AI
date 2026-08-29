import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.models.marketing import Campaign
from app.services.attribution_service import AttributionService
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/campaigns", tags=["marketing-campaigns"])


def _campaign_to_dict(c: Campaign) -> dict[str, Any]:
    return {
        "id": str(c.id), "name": c.name, "channel": c.channel, "objective": c.objective, "status": c.status,
        "monthly_budget": str(c.monthly_budget) if c.monthly_budget is not None else None,
        "total_budget": str(c.total_budget) if c.total_budget is not None else None,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "external_provider": c.external_provider,
    }


@router.get("")
async def list_campaigns(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rows = (
        await db.execute(select(Campaign).where(Campaign.tenant_id == current_user.tenant_id).order_by(Campaign.created_at.desc()))
    ).scalars().all()
    return {"campaigns": [_campaign_to_dict(c) for c in rows]}


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != current_user.tenant_id:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")

    attribution_service = AttributionService(async_session_maker)
    perf = await attribution_service.campaign_performance(current_user.tenant_id, campaign_id)
    result = _campaign_to_dict(campaign)
    result["performance"] = {
        "spend": str(perf.spend), "leads": perf.leads, "qualified_leads": perf.qualified_leads,
        "booked": perf.booked, "jobs_created": perf.jobs_created, "jobs_closed": perf.jobs_closed,
        "invoiced_count": perf.invoiced_count, "revenue": str(perf.revenue),
        "collected_revenue": str(perf.collected_revenue),
        "cac": str(perf.cac) if perf.cac is not None else None, "cac_note": perf.cac_note,
        "revenue_per_lead": str(perf.revenue_per_lead) if perf.revenue_per_lead is not None else None,
        "roas": str(perf.roas) if perf.roas is not None else None, "roas_note": perf.roas_note,
    }
    return result


@router.post("")
async def create_campaign(
    body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    """body = {name, channel, objective?, monthly_budget?, total_budget?, start_date?, end_date?, external_provider?}"""
    try:
        output = await registry.execute("marketing.create_campaign", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{campaign_id}/status")
async def set_campaign_status(
    campaign_id: uuid.UUID, status: str, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.set_campaign_status", {"campaign_id": str(campaign_id), "status": status}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{campaign_id}/spend")
async def record_spend(
    campaign_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {channel, amount, spend_date, source?, external_reference?} — allocates 100% to this campaign."""
    payload = {
        "channel": body["channel"], "amount": body["amount"], "spend_date": body["spend_date"],
        "allocations": [{"campaign_id": str(campaign_id), "amount": body["amount"]}],
        "source": body.get("source", "manual"), "external_reference": body.get("external_reference"),
    }
    try:
        output = await registry.execute("marketing.record_spend", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/{campaign_id}/budget")
async def get_budget_status(
    campaign_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.get_budget_status", {"campaign_id": str(campaign_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/detect-exceptions")
async def detect_performance_exceptions(
    current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.detect_performance_exceptions", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
