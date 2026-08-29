"""Small shared helper so finance services (and any future service that needs
to create an ApprovalRequest directly, outside of ToolRegistry's own
APPROVAL_REQUIRED path) all go through the exact same persisted model —
never a second approval mechanism. Mirrors the row shape created in
`app/tools/registry.py`.
"""

import uuid
from typing import Any

from app.models.approval import ApprovalRequest, ApprovalStatus


async def create_approval_request(
    session,
    *,
    tenant_id: uuid.UUID,
    requested_by_type: str,
    requested_by_id: uuid.UUID | None,
    tool_name: str,
    action_type: str,
    reason: str,
    tool_input: dict[str, Any],
    correlation_id: uuid.UUID | None = None,
    requested_by_role: str | None = None,
) -> uuid.UUID:
    request = ApprovalRequest(
        tenant_id=tenant_id,
        requested_by_type=requested_by_type,
        requested_by_id=requested_by_id,
        requested_by_role=requested_by_role,
        tool_name=tool_name,
        action_type=action_type,
        reason=reason,
        tool_input=tool_input,
        status=ApprovalStatus.PENDING,
        correlation_id=correlation_id,
        idempotency_key=f"approval-exec-{correlation_id or uuid.uuid4()}",
    )
    session.add(request)
    await session.flush()
    return request.id
