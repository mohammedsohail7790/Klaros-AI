"""marketing.list_campaigns: a read-only list of the business's own campaigns, so an agent can find a campaign id instead of guessing one."""

import uuid

import pytest

from app.api.tool_deps import get_tool_registry
from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_halla_integration import _h, _register, _tenant_id

pytestmark = pytest.mark.asyncio


def _ctx(tid):
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _make(client, token, name, status="ACTIVE", channel="GOOGLE_ADS"):
    r = await client.post("/api/v1/marketing/campaigns", json={"name": name, "channel": channel, "objective": "LEAD_GEN", "monthly_budget": 500}, headers=_h(token))
    assert r.status_code in (200, 201), r.text
    cid = (r.json().get("campaign") or r.json())["id"]
    if status != "DRAFT":
        assert (await client.post(f"/api/v1/marketing/campaigns/{cid}/status", params={"status": status}, headers=_h(token))).status_code == 200
    return cid


async def test_it_is_a_read_only_tool_that_runs_without_approval() -> None:
    assert DEFAULT_TOOL_POLICIES["marketing.list_campaigns"] == ActionPolicy.AUTO
    assert get_tool_registry().get("marketing.list_campaigns").required_permission.value == "READ_MARKETING"


async def test_it_lists_only_this_businesss_campaigns_and_can_filter_by_status(client) -> None:
    token = await _register(client, "Camp Co", "campco@example.com")
    other = await _register(client, "Other Camp Co", "othercamp@example.com")
    tid, other_tid = await _tenant_id(client, token), await _tenant_id(client, other)
    a = await _make(client, token, "Spring search", "ACTIVE")
    await _make(client, token, "Old brand", "PAUSED")
    await _make(client, other, "Someone else's", "ACTIVE")

    everything = await get_tool_registry().execute("marketing.list_campaigns", {}, _ctx(tid))
    assert everything.total == 2 and {c["name"] for c in everything.campaigns} == {"Spring search", "Old brand"}
    active = await get_tool_registry().execute("marketing.list_campaigns", {"status": "ACTIVE"}, _ctx(tid))
    assert [c["id"] for c in active.campaigns] == [a] and active.total == 1
    assert (await get_tool_registry().execute("marketing.list_campaigns", {}, _ctx(other_tid))).total == 1
    empty = await get_tool_registry().execute("marketing.list_campaigns", {"limit": 1}, _ctx(tid))
    assert len(empty.campaigns) == 1 and empty.total == 2
