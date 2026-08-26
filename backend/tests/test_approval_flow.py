import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolBlockedError

pytestmark = pytest.mark.asyncio


async def test_approval_required_tool_blocks_then_api_approves(client, tool_registry) -> None:
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
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    exec_resp = await client.post(
        "/api/v1/tools/notifications.create_notification/execute",
        json={"input": {"title": "Needs approval", "body": "test"}},
        headers=headers,
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["status"] == "pending_approval"
    approval_id = exec_resp.json()["approval_request_id"]

    pending = await client.get("/api/v1/approvals", headers=headers)
    assert pending.status_code == 200
    assert any(a["id"] == approval_id for a in pending.json())

    decide = await client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json={"decision_note": "looks fine"},
        headers=headers,
    )
    assert decide.status_code == 200
    assert decide.json()["status"] == "APPROVED"

    again = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers)
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
