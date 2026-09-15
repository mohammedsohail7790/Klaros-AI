import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.finance import Refund
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/refunds", tags=["refunds"])


def _refund_to_dict(r: Refund) -> dict[str, Any]:
    return {
        "id": str(r.id), "payment_id": str(r.payment_id), "invoice_id": str(r.invoice_id) if r.invoice_id else None,
        "amount": str(r.amount), "reason": r.reason, "status": r.status,
    }


@router.get("")
async def list_refunds(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rows = (
        await db.execute(select(Refund).where(Refund.tenant_id == current_user.tenant_id).order_by(Refund.created_at.desc()))
    ).scalars().all()
    return {"refunds": [_refund_to_dict(r) for r in rows]}


@router.post("")
async def create_refund_request(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {payment_id, invoice_id?, amount, reason}. Never issues a
    refund directly — always creates a pending, human-approvable request."""
    try:
        output = await registry.execute("finance.create_refund_request", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{refund_id}/approve")
async def approve_refund(
    refund_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.approve_refund", {"refund_id": str(refund_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{refund_id}/sync-to-quickbooks")
async def sync_refund_to_quickbooks(
    refund_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.sync_refund_to_quickbooks", {"refund_id": str(refund_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{refund_id}/reject")
async def reject_refund(
    refund_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.reject_refund", {"refund_id": str(refund_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
