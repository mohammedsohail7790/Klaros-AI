import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.db.session import async_session_maker, get_db
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.rbac import Permission
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    tool_name: str
    action_type: str
    reason: str
    status: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ApprovalResponse])
async def list_pending_approvals(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalResponse]:
    rows = (
        await db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == current_user.tenant_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        )
    ).scalars().all()
    return [ApprovalResponse.model_validate(r) for r in rows]


class DecideApprovalRequest(BaseModel):
    decision_note: str | None = None


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: uuid.UUID,
    body: DecideApprovalRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.APPROVE_SPEND)),
) -> ApprovalResponse:
    return await _decide(approval_id, current_user, ApprovalStatus.APPROVED, body.decision_note)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: uuid.UUID,
    body: DecideApprovalRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.APPROVE_SPEND)),
) -> ApprovalResponse:
    return await _decide(approval_id, current_user, ApprovalStatus.REJECTED, body.decision_note)


async def _decide(
    approval_id: uuid.UUID, current_user: CurrentUser, decision: str, note: str | None
) -> ApprovalResponse:
    async with async_session_maker() as session:
        request = await session.get(ApprovalRequest, approval_id)
        if request is None or request.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
        if request.status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Approval already decided"
            )
        request.status = decision
        request.decided_by = current_user.id
        request.decision_note = note
        await session.commit()
        await session.refresh(request)
        return ApprovalResponse.model_validate(request)
