import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.operations import ExceptionStatus, OperationsException
from app.services.delay_detection_service import DelayDetectionService
from app.services.exception_service import ExceptionService
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


def _exception_to_dict(e: OperationsException) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "type": e.type,
        "severity": e.severity,
        "entity_type": e.entity_type,
        "entity_id": str(e.entity_id),
        "description": e.description,
        "recommended_action": e.recommended_action,
        "status": e.status,
        "created_at": e.created_at.isoformat(),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


@router.get("")
async def list_exceptions(
    status: str | None = Query(default=ExceptionStatus.OPEN.value),
    severity: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(OperationsException).where(OperationsException.tenant_id == current_user.tenant_id)
    if status:
        query = query.where(OperationsException.status == status)
    if severity:
        query = query.where(OperationsException.severity == severity)
    query = query.order_by(OperationsException.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"exceptions": [_exception_to_dict(e) for e in rows]}


@router.post("/{exception_id}/resolve")
async def resolve_exception(
    exception_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "operations.resolve_exception", {"exception_id": str(exception_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/detect")
async def run_delay_detection(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, int]:
    """section 23: deterministic delay detection, run on demand. Production
    would call this from a scheduler; no such scheduler exists yet, so it's
    exposed here to run manually/on a client-side poll instead of pretending
    an automatic cron job is already wired up."""
    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus

    bus = get_event_bus()
    exception_service = ExceptionService(async_session_maker, bus)
    delay_service = DelayDetectionService(async_session_maker, bus, exception_service)
    return await delay_service.run(current_user.tenant_id)
