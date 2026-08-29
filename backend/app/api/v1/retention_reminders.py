import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.retention import ServiceReminder
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/retention/reminders", tags=["retention-reminders"])


def _to_dict(r: ServiceReminder) -> dict[str, Any]:
    return {
        "id": str(r.id), "customer_id": str(r.customer_id), "source_job_id": str(r.source_job_id) if r.source_job_id else None,
        "service_type": r.service_type, "reminder_date": r.reminder_date.isoformat(), "reason": r.reason, "status": r.status,
    }


@router.get("")
async def list_reminders(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (
        await db.execute(select(ServiceReminder).where(ServiceReminder.tenant_id == current_user.tenant_id).order_by(ServiceReminder.reminder_date))
    ).scalars().all()
    return {"reminders": [_to_dict(r) for r in rows]}


@router.post("/mark-due")
async def mark_due(current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.mark_due_reminders", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{reminder_id}/status")
async def update_status(
    reminder_id: uuid.UUID, status: str, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "retention.update_reminder_status", {"reminder_id": str(reminder_id), "status": status}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
