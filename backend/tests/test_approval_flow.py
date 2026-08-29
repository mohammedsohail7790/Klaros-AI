import uuid

import pytest

from app.core.security import create_access_token, hash_password
from app.models.actor import ActorType
from app.models.rbac import Role
from app.models.user import User
from app.tools.base import ExecutionContext
from app.tools.errors import ToolBlockedError

pytestmark = pytest.mark.asyncio


async def _create_second_user(async_session_maker, tenant_id: uuid.UUID, *, role: Role) -> str:
    """A real second user under the same tenant, bypassing the register/login
    HTTP round trip (which only ever creates the first/OWNER user) — the
    self-approval rule (Phase 9) means the approve step needs a genuinely
    different actor from whoever triggered the approval-required action."""
    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id,
            email=f"{uuid.uuid4().hex[:8]}@approvalflow.com",
            hashed_password=hash_password("supersecret1"),
            full_name="Second User",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return create_access_token(user.id, tenant_id, role.value)


async def test_approval_required_tool_blocks_then_api_approves(client, tool_registry) -> None:
    from app.db.session import async_session_maker
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED

    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Approval Flow Co",
            "full_name": "Owner",
            "email": "owner@approvalflow.com",
            "password": "supersecret1",
        },
    )
    requester_token = register.json()["tokens"]["access_token"]
    tenant_id = uuid.UUID(register.json()["user"]["tenant_id"])
    requester_headers = {"Authorization": f"Bearer {requester_token}"}

    approver_token = await _create_second_user(async_session_maker, tenant_id, role=Role.OWNER)
    approver_headers = {"Authorization": f"Bearer {approver_token}"}

    exec_resp = await client.post(
        "/api/v1/tools/notifications.create_notification/execute",
        json={"input": {"title": "Needs approval", "body": "test"}},
        headers=requester_headers,
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["status"] == "pending_approval"
    approval_id = exec_resp.json()["approval_request_id"]

    pending = await client.get("/api/v1/approvals", headers=requester_headers)
    assert pending.status_code == 200
    assert any(a["id"] == approval_id for a in pending.json()["approvals"])

    # The requester themselves cannot approve their own request.
    self_approve = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", json={}, headers=requester_headers
    )
    assert self_approve.status_code == 403

    decide = await client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json={"decision_note": "looks fine"},
        headers=approver_headers,
    )
    assert decide.status_code == 200
    assert decide.json()["status"] == "APPROVED"
    # The original action actually resumed and executed — not a dead end.
    assert decide.json()["execution_status"] == "EXECUTED"

    again = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=approver_headers)
    assert again.status_code == 409


async def test_blocked_policy_prevents_direct_tool_call(tool_registry) -> None:
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.BLOCKED

    context = ExecutionContext(
        tenant_id=uuid.uuid4(), actor_type=ActorType.AI, actor_id=None, role=Role.OWNER
    )
    with pytest.raises(ToolBlockedError):
        await tool_registry.execute(
            "notifications.create_notification", {"title": "x", "body": "y"}, context
        )
