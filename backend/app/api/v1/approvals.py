import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _raise_for_service_error(exc: Exception) -> None:
    """`ApprovalExecutionService` raises its own typed exceptions
    (`ApprovalStateError`/`SelfApprovalError`/`ApprovalNotFoundError`)
    directly from inside the `approvals.*` tools' `execute()` — `Tool`
    doesn't require wrapping in `ValueError`, and `ToolRegistry.execute()`'s
    generic `except Exception: audit; raise` re-raises them unchanged.
    Approval-specific state conflicts map to 409, not the generic 404
    `raise_http_for_tool_error` gives a bare `ValueError`.
    """
    from fastapi import HTTPException, status

    from app.services.approval_execution_service import (
        ApprovalNotFoundError,
        ApprovalStateError,
        SelfApprovalError,
    )

    if isinstance(exc, ApprovalStateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, SelfApprovalError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ApprovalNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raise_http_for_tool_error(exc)


@router.get("")
async def list_approvals(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "approvals.list", {"status": status_filter}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    # Preserve the pre-Phase-9 response shape (`id`, not `approval_request_id`)
    # for the one existing consumer (tests/test_approval_flow.py) while
    # adding the new execution fields.
    return {
        "approvals": [
            {
                "id": a["approval_request_id"],
                "tool_name": a["tool_name"],
                "action_type": a["action_type"],
                "reason": a["reason"],
                "status": a["status"],
                "execution_status": a["execution_status"],
                "requested_by_type": a["requested_by_type"],
                "created_at": a["created_at"],
                "decided_at": a["decided_at"],
                "executed_at": a["executed_at"],
            }
            for a in output.model_dump(mode="json")["approvals"]
        ]
    }


@router.get("/{approval_id}")
async def get_approval_detail(
    approval_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "approvals.get_detail", {"approval_request_id": str(approval_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class DecideApprovalRequest(BaseModel):
    decision_note: str | None = None


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: uuid.UUID,
    body: DecideApprovalRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "approvals.approve",
            {"approval_request_id": str(approval_id), "note": body.decision_note},
            execution_context(current_user),
        )
    except Exception as exc:  # noqa: BLE001 — dispatched to a precise status code below, never swallowed
        _raise_for_service_error(exc)
    return output.model_dump(mode="json")


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: uuid.UUID,
    body: DecideApprovalRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "approvals.reject",
            {"approval_request_id": str(approval_id), "note": body.decision_note},
            execution_context(current_user),
        )
    except Exception as exc:  # noqa: BLE001 — dispatched to a precise status code below, never swallowed
        _raise_for_service_error(exc)
    return output.model_dump(mode="json")


@router.post("/{approval_id}/retry")
async def retry_execution(
    approval_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "approvals.retry_execution",
            {"approval_request_id": str(approval_id)},
            execution_context(current_user),
        )
    except Exception as exc:  # noqa: BLE001 — dispatched to a precise status code below, never swallowed
        _raise_for_service_error(exc)
    return output.model_dump(mode="json")
