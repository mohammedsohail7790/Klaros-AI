import uuid
from typing import Any

from pydantic import BaseModel

from app.models.operations import ScopeChange
from app.models.rbac import Permission
from app.services.scope_change_service import ScopeChangeService
from app.tools.base import ExecutionContext, Tool


def _scope_change_to_dict(s: ScopeChange) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "job_id": str(s.job_id),
        "description": s.description,
        "reason": s.reason,
        "estimated_cost": float(s.estimated_cost) if s.estimated_cost is not None else None,
        "estimated_revenue": float(s.estimated_revenue) if s.estimated_revenue is not None else None,
        "margin_impact": float(s.margin_impact) if s.margin_impact is not None else None,
        "status": s.status,
    }


class CreateScopeChangeInput(BaseModel):
    job_id: uuid.UUID
    description: str
    reason: str | None = None
    estimated_cost: float | None = None
    estimated_revenue: float | None = None


class ScopeChangeOutput(BaseModel):
    scope_change: dict[str, Any]


class CreateScopeChange(Tool):
    name = "operations.create_scope_change"
    description = (
        "Record a detected scope change and its estimated cost/revenue/margin impact. "
        "Never modifies the job's own pricing fields."
    )
    input_schema = CreateScopeChangeInput
    output_schema = ScopeChangeOutput
    required_permission = Permission.UPDATE_JOB

    def __init__(self, scope_change_service: ScopeChangeService) -> None:
        self._scope_change_service = scope_change_service

    async def execute(self, input: CreateScopeChangeInput, context: ExecutionContext) -> ScopeChangeOutput:
        scope_change = await self._scope_change_service.create_scope_change(
            context.tenant_id,
            input.job_id,
            description=input.description,
            reason=input.reason,
            estimated_cost=input.estimated_cost,
            estimated_revenue=input.estimated_revenue,
            created_by=context.actor_id,
        )
        return ScopeChangeOutput(scope_change=_scope_change_to_dict(scope_change))


class RequestScopeChangeApprovalInput(BaseModel):
    scope_change_id: uuid.UUID
    justification: str


class RequestScopeChangeApprovalOutput(BaseModel):
    acknowledged: bool


class RequestScopeChangeApproval(Tool):
    """Policy for this tool is APPROVAL_REQUIRED by default (see
    app/tools/policy.py) — calling it creates an ApprovalRequest via the
    registry's own enforcement and this body never runs in that path. It
    exists so scope changes with a cost/revenue impact have an explicit,
    auditable request-for-approval action rather than silently proceeding.
    """

    name = "operations.request_scope_change_approval"
    description = "Request human approval for a scope change with a cost/revenue impact."
    input_schema = RequestScopeChangeApprovalInput
    output_schema = RequestScopeChangeApprovalOutput
    required_permission = Permission.APPROVE_SCOPE_CHANGE

    async def execute(
        self, input: RequestScopeChangeApprovalInput, context: ExecutionContext
    ) -> RequestScopeChangeApprovalOutput:
        return RequestScopeChangeApprovalOutput(acknowledged=True)
