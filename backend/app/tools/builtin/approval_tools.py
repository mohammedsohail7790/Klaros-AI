from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

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
            request = ApprovalRequest(
                tenant_id=context.tenant_id,
                requested_by_type=context.actor_type,
                requested_by_id=context.actor_id,
                tool_name=input.tool_name,
                action_type=input.action_type,
                reason=input.reason,
                tool_input=input.tool_input,
                status=ApprovalStatus.PENDING,
                correlation_id=context.correlation_id,
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)
            return CreateApprovalRequestOutput(
                approval_request_id=str(request.id), status=request.status
            )
