import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.finance import CreditNote
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/credit-notes", tags=["credit-notes"])


def _to_dict(c: CreditNote) -> dict[str, Any]:
    return {
        "id": str(c.id), "invoice_id": str(c.invoice_id), "credit_note_number": c.credit_note_number,
        "reason": c.reason, "total": str(c.total), "status": c.status,
    }


@router.get("")
async def list_credit_notes(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(CreditNote).where(CreditNote.tenant_id == current_user.tenant_id).order_by(CreditNote.created_at.desc())
        )
    ).scalars().all()
    return {"credit_notes": [_to_dict(c) for c in rows]}


@router.post("")
async def create_credit_note_request(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {invoice_id, reason, line_items: [{description, amount}]}"""
    try:
        output = await registry.execute("finance.create_credit_note_request", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{credit_note_id}/approve")
async def approve_credit_note(
    credit_note_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.approve_credit_note", {"credit_note_id": str(credit_note_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{credit_note_id}/reject")
async def reject_credit_note(
    credit_note_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "finance.reject_credit_note", {"credit_note_id": str(credit_note_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
