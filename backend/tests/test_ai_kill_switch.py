"""The AI kill switch (Organization.ai_paused) — a real, org-wide
emergency control enforced in ToolRegistry.execute() for every non-USER
actor. Confirms: AI/automation calls are blocked while paused, a human's
own direct action is never affected, only an OWNER can flip the switch,
and a tenant with no Organization row is never gated (same backward-
compat rule as the billing gate)."""

import uuid

import pytest

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.organization import Organization
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolKillSwitchError, ToolPermissionError

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, *, actor_type=ActorType.USER, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


async def _make_org(*, ai_paused: bool = False) -> Organization:
    async with async_session_maker() as session:
        org = Organization(
            name=f"Kill Switch Co {uuid.uuid4().hex[:6]}", slug=f"kill-switch-{uuid.uuid4().hex}",
            ai_paused=ai_paused, plan="growth", billing_status="active",
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


async def test_ai_actor_blocked_when_paused(tool_registry) -> None:
    org = await _make_org(ai_paused=True)
    with pytest.raises(ToolKillSwitchError):
        await tool_registry.execute(
            "insights.generate_morning_brief", {}, _ctx(org.id, actor_type=ActorType.AI)
        )


async def test_workflow_actor_blocked_when_paused(tool_registry) -> None:
    org = await _make_org(ai_paused=True)
    with pytest.raises(ToolKillSwitchError):
        await tool_registry.execute(
            "insights.generate_morning_brief", {}, _ctx(org.id, actor_type=ActorType.WORKFLOW)
        )


async def test_human_actor_unaffected_by_kill_switch(tool_registry) -> None:
    """generate_morning_brief is a poor case for this — it internally
    delegates its own data-gathering sub-calls through AIExecutionService
    with a hardcoded ActorType.AI regardless of who triggered the outer
    call (see app/services/morning_brief_service.py::_call), so it's
    correctly blocked by the kill switch even when a human asked for it.
    crm.create_customer is a pure, non-AI-delegating human action —
    the right case to prove a direct human action is never touched."""
    org = await _make_org(ai_paused=True)
    out = await tool_registry.execute(
        "crm.create_customer", {"name": "Kill Switch Test Customer"}, _ctx(org.id, actor_type=ActorType.USER)
    )
    assert out.customer["name"] == "Kill Switch Test Customer"


async def test_ai_actor_not_blocked_when_not_paused(tool_registry) -> None:
    org = await _make_org(ai_paused=False)
    out = await tool_registry.execute(
        "insights.generate_morning_brief", {}, _ctx(org.id, actor_type=ActorType.AI)
    )
    assert out.headline


async def test_tenant_with_no_organization_row_is_never_gated(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    out = await tool_registry.execute(
        "insights.generate_morning_brief", {}, _ctx(tenant_id, actor_type=ActorType.AI)
    )
    assert out.headline


async def test_owner_can_toggle_kill_switch(tool_registry) -> None:
    org = await _make_org()
    on = await tool_registry.execute("organization.set_kill_switch", {"active": True}, _ctx(org.id, role=Role.OWNER))
    assert on.ai_paused is True
    assert on.ai_paused_by is not None

    off = await tool_registry.execute("organization.set_kill_switch", {"active": False}, _ctx(org.id, role=Role.OWNER))
    assert off.ai_paused is False
    assert off.ai_paused_by is None


async def test_non_owner_cannot_toggle_kill_switch(tool_registry) -> None:
    org = await _make_org()
    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("organization.set_kill_switch", {"active": True}, _ctx(org.id, role=Role.MANAGER))


async def test_status_reflects_toggle(tool_registry) -> None:
    org = await _make_org()
    await tool_registry.execute("organization.set_kill_switch", {"active": True}, _ctx(org.id, role=Role.OWNER))
    status = await tool_registry.execute("organization.get_kill_switch_status", {}, _ctx(org.id))
    assert status.ai_paused is True


async def test_kill_switch_over_http(client) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "HTTP Kill Switch Co", "full_name": "Owner Test",
            "email": "owner@httpkillswitch.com", "password": "supersecret1",
        },
    )
    assert register.status_code == 201
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    status_resp = await client.get("/api/v1/organization/kill-switch", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["ai_paused"] is False

    set_resp = await client.post("/api/v1/organization/kill-switch", json={"active": True}, headers=headers)
    assert set_resp.status_code == 200
    assert set_resp.json()["ai_paused"] is True
