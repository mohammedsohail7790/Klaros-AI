import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.retention import RetentionOpportunity
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/retention/opportunities", tags=["retention-opportunities"])


def _to_dict(o: RetentionOpportunity) -> dict[str, Any]:
    return {
        "id": str(o.id), "customer_id": str(o.customer_id), "type": o.type, "reason": o.reason,
        "detected_at": o.detected_at.isoformat(), "priority": o.priority, "status": o.status,
        "source_event": o.source_event, "recommended_action": o.recommended_action,
    }


@router.get("")
async def list_opportunities(
    status: str | None = None, customer_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(RetentionOpportunity).where(RetentionOpportunity.tenant_id == current_user.tenant_id)
    if status:
        query = query.where(RetentionOpportunity.status == status)
    if customer_id:
        query = query.where(RetentionOpportunity.customer_id == customer_id)
    rows = (await db.execute(query.order_by(RetentionOpportunity.detected_at.desc()))).scalars().all()
    return {"opportunities": [_to_dict(o) for o in rows]}


@router.post("/{opportunity_id}/status")
async def update_status(
    opportunity_id: uuid.UUID, status: str, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "retention.update_opportunity_status", {"opportunity_id": str(opportunity_id), "status": status},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
