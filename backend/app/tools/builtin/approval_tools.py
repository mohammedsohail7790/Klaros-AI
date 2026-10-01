import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.tools.base import ExecutionContext, Tool


class CreateApprovalRequestInput(BaseModel):
    tool_name: str
    action_type: str
    reason: str
    tool_input: dict[str, Any] = {}


class CreateApprovalRequestOutput(BaseModel):
    approval_request_id: str
    status: str


class CreateApprovalRequest(Tool):
    """Direct entry point for creating an approval request outside of the
    registry's own APPROVAL_REQUIRED interception — e.g. for a workflow step
    that wants to pause for a human decision that isn't itself a tool call.
    """

    name = "approvals.create_request"
    description = "Create a pending approval request for a human to decide."
    input_schema = CreateApprovalRequestInput
    output_schema = CreateApprovalRequestOutput

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(
        self, input: CreateApprovalRequestInput, context: ExecutionContext
    ) -> CreateApprovalRequestOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            request = ApprovalRequest(
                tenant_id=context.tenant_id,
                requested_by_type=context.actor_type,
                requested_by_id=context.actor_id,
                requested_by_role=context.role.value if context.role else None,
                tool_name=input.tool_name,
                action_type=input.action_type,
                reason=input.reason,
                tool_input=input.tool_input,
                status=ApprovalStatus.PENDING,
                correlation_id=context.correlation_id,
                idempotency_key=f"approval-exec-{context.correlation_id or uuid.uuid4()}",
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)
            return CreateApprovalRequestOutput(
                approval_request_id=str(request.id), status=request.status
            )


class ApprovalOutput(BaseModel):
    approval_request_id: str
    status: str
    execution_status: str
    execution_result: dict[str, Any] | None = None
    execution_error: str | None = None


def _to_output(request) -> "ApprovalOutput":
    return ApprovalOutput(
        approval_request_id=str(request.id),
        status=request.status,
        execution_status=request.execution_status,
        execution_result=request.execution_result,
        execution_error=request.execution_error,
    )


class ApproveActionInput(BaseModel):
    approval_request_id: uuid.UUID
    note: str | None = None


class ApproveAction(Tool):
    """Approving here does not just flip a status flag — it resumes and
    actually executes the original tool call through ToolRegistry (see
    ApprovalExecutionService). The AI must never call this tool: it is
    gated by APPROVE_ACTIONS, a permission never granted to the AI actor
    context anywhere in this codebase, and ApprovalExecutionService itself
    independently refuses a requester approving their own request."""

    name = "approvals.approve"
    description = "Approve a pending approval request; resumes and executes the original action."
    input_schema = ApproveActionInput
    output_schema = ApprovalOutput
    required_permission = None  # checked below via APPROVE_ACTIONS after AI-actor guard

    def __init__(self, execution_service) -> None:
        self._execution_service = execution_service

    async def execute(self, input: ApproveActionInput, context: ExecutionContext) -> ApprovalOutput:
        from app.models.actor import ActorType
        from app.models.rbac import Permission, role_has_permission

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot approve actions — approval requires a human actor")
        if context.role is None or not role_has_permission(context.role, Permission.APPROVE_ACTIONS):
            raise ValueError("Actor lacks required permission: APPROVE_ACTIONS")

        request = await self._execution_service.approve(
            context.tenant_id,
            input.approval_request_id,
            decided_by_id=context.actor_id,
            decided_by_role=context.role,
            note=input.note,
        )
        return _to_output(request)


class RejectActionInput(BaseModel):
    approval_request_id: uuid.UUID
    note: str | None = None


class RejectAction(Tool):
    name = "approvals.reject"
    description = "Reject a pending approval request; the original action never executes."
    input_schema = RejectActionInput
    output_schema = ApprovalOutput
    required_permission = None

    def __init__(self, execution_service) -> None:
        self._execution_service = execution_service

    async def execute(self, input: RejectActionInput, context: ExecutionContext) -> ApprovalOutput:
        from app.models.actor import ActorType
        from app.models.rbac import Permission, role_has_permission

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot reject actions — this requires a human actor")
        if context.role is None or not role_has_permission(context.role, Permission.REJECT_ACTIONS):
            raise ValueError("Actor lacks required permission: REJECT_ACTIONS")

        request = await self._execution_service.reject(
            context.tenant_id, input.approval_request_id, decided_by_id=context.actor_id, note=input.note
        )
        return _to_output(request)


class RetryApprovalExecutionInput(BaseModel):
    approval_request_id: uuid.UUID


class RetryApprovalExecution(Tool):
    name = "approvals.retry_execution"
    description = "Retry a FAILED approved-action execution. Idempotent — a second concurrent retry is a safe no-op."
    input_schema = RetryApprovalExecutionInput
    output_schema = ApprovalOutput
    required_permission = None

    def __init__(self, execution_service) -> None:
        self._execution_service = execution_service

    async def execute(self, input: RetryApprovalExecutionInput, context: ExecutionContext) -> ApprovalOutput:
        from app.models.rbac import Permission, role_has_permission

        if context.role is None or not role_has_permission(context.role, Permission.EXECUTE_APPROVED_ACTIONS):
            raise ValueError("Actor lacks required permission: EXECUTE_APPROVED_ACTIONS")

        request = await self._execution_service.retry_failed(
            context.tenant_id, input.approval_request_id, actor_id=context.actor_id
        )
        return _to_output(request)


class ListApprovalsInput(BaseModel):
    status: str | None = None
    limit: int = 50


class ApprovalSummary(BaseModel):
    approval_request_id: str
    tool_name: str
    action_type: str
    reason: str
    status: str
    execution_status: str
    requested_by_type: str
    created_at: str
    decided_at: str | None
    executed_at: str | None


class ListApprovalsOutput(BaseModel):
    approvals: list[ApprovalSummary]


class ListApprovals(Tool):
    name = "approvals.list"
    description = "List approval requests for the tenant, optionally filtered by status."
    input_schema = ListApprovalsInput
    output_schema = ListApprovalsOutput
    required_permission = None

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: ListApprovalsInput, context: ExecutionContext) -> ListApprovalsOutput:
        from sqlalchemy import select

        from app.models.rbac import Permission, role_has_permission

        if context.role is None or not role_has_permission(context.role, Permission.READ_APPROVALS):
            raise ValueError("Actor lacks required permission: READ_APPROVALS")

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            query = select(ApprovalRequest).where(ApprovalRequest.tenant_id == context.tenant_id)
            if input.status:
                query = query.where(ApprovalRequest.status == input.status)
            query = query.order_by(ApprovalRequest.created_at.desc()).limit(min(input.limit, 200))
            rows = (await session.execute(query)).scalars().all()
            return ListApprovalsOutput(
                approvals=[
                    ApprovalSummary(
                        approval_request_id=str(r.id),
                        tool_name=r.tool_name,
                        action_type=r.action_type,
                        reason=r.reason,
                        status=r.status,
                        execution_status=r.execution_status,
                        requested_by_type=r.requested_by_type,
                        created_at=r.created_at.isoformat(),
                        decided_at=r.decided_at.isoformat() if r.decided_at else None,
                        executed_at=r.executed_at.isoformat() if r.executed_at else None,
                    )
                    for r in rows
                ]
            )


class GetApprovalDetailInput(BaseModel):
    approval_request_id: uuid.UUID


class ApprovalDetailOutput(BaseModel):
    approval_request_id: str
    tenant_id: str
    tool_name: str
    action_type: str
    reason: str
    tool_input: dict[str, Any]
    status: str
    requested_by_type: str
    requested_by_id: str | None
    decided_by: str | None
    decision_note: str | None
    execution_status: str
    execution_result: dict[str, Any] | None
    execution_error: str | None
    execution_attempts: int
    created_at: str
    decided_at: str | None
    executed_at: str | None


class GetApprovalDetail(Tool):
    name = "approvals.get_detail"
    description = "Fetch full detail for one approval request, for the /approvals admin page."
    input_schema = GetApprovalDetailInput
    output_schema = ApprovalDetailOutput
    required_permission = None

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetApprovalDetailInput, context: ExecutionContext) -> ApprovalDetailOutput:
        from app.models.rbac import Permission, role_has_permission

        if context.role is None or not role_has_permission(context.role, Permission.READ_APPROVALS):
            raise ValueError("Actor lacks required permission: READ_APPROVALS")

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            request = await session.get(ApprovalRequest, input.approval_request_id)
            if request is None or request.tenant_id != context.tenant_id:
                raise ValueError("Approval request not found")
            return ApprovalDetailOutput(
                approval_request_id=str(request.id),
                tenant_id=str(request.tenant_id),
                tool_name=request.tool_name,
                action_type=request.action_type,
                reason=request.reason,
                tool_input=request.tool_input,
                status=request.status,
                requested_by_type=request.requested_by_type,
                requested_by_id=str(request.requested_by_id) if request.requested_by_id else None,
                decided_by=str(request.decided_by) if request.decided_by else None,
                decision_note=request.decision_note,
                execution_status=request.execution_status,
                execution_result=request.execution_result,
                execution_error=request.execution_error,
                execution_attempts=request.execution_attempts,
                created_at=request.created_at.isoformat(),
                decided_at=request.decided_at.isoformat() if request.decided_at else None,
                executed_at=request.executed_at.isoformat() if request.executed_at else None,
            )
