import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.finance import WriteOffRequest
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/writeoffs", tags=["writeoffs"])


def _to_dict(w: WriteOffRequest) -> dict[str, Any]:
    return {
        "id": str(w.id), "invoice_id": str(w.invoice_id), "amount": str(w.amount),
        "reason": w.reason, "status": w.status,
    }


@router.get("")
async def list_writeoffs(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(WriteOffRequest)
            .where(WriteOffRequest.tenant_id == current_user.tenant_id)
            .order_by(WriteOffRequest.created_at.desc())
        )
    ).scalars().all()
    return {"writeoffs": [_to_dict(w) for w in rows]}


@router.post("")
async def create_writeoff_request(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {invoice_id, amount, reason}"""
    try:
        output = await registry.execute("finance.create_writeoff_request", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{writeoff_id}/approve")
async def approve_writeoff(
    writeoff_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.approve_writeoff", {"writeoff_id": str(writeoff_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{writeoff_id}/reject")
async def reject_writeoff(
    writeoff_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.reject_writeoff", {"writeoff_id": str(writeoff_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
