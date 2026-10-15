"""The Marketing Manager keeps content drafts flowing for approval, and Growth Scout no longer fails when there are no campaigns."""

import uuid

import pytest

from app.api.tool_deps import get_tool_registry
from app.models.actor import ActorType
from app.models.rbac import Role
from app.services import operating_crew as oc
from app.tools.base import ExecutionContext
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_halla_integration import _register, _tenant_id

pytestmark = pytest.mark.asyncio


def _ctx(tid):
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.AI, actor_id=uuid.uuid4(), role=Role.MANAGER)


def _member(key):
    return next(m for m in oc.GENERIC_CREW if m.key == key)


async def test_every_tool_the_marketing_manager_uses_exists_and_content_is_never_published_by_it() -> None:
    manager = _member("marketing-manager")
    registry = get_tool_registry()
    for tool in manager.tools:
        registry.get(tool)
    assert "marketing.publish_content_variant" not in manager.tools and "marketing.approve_content" not in manager.tools
    assert DEFAULT_TOOL_POLICIES["marketing.list_content"] == ActionPolicy.AUTO
    assert manager.max_tool_chain_depth >= 20
    for rule in ("never state a price", "never name a clinic or doctor", "a person approves content first"):
        assert rule in manager.instructions


async def test_the_manager_lists_existing_content_and_a_draft_goes_to_approval_not_to_publication(client) -> None:
    token = await _register(client, "Content Co", "contentco@example.com")
    tid = await _tenant_id(client, token)
    reg = get_tool_registry()
    assert (await reg.execute("marketing.list_content", {}, _ctx(tid))).content == []
    idea = await reg.execute("marketing.create_content_idea", {"title": "How to compare clinics", "summary": "A checklist"}, _ctx(tid))
    cid = idea.content["id"]
    await reg.execute("marketing.add_content_variant", {"content_id": cid, "channel": "Blog", "body_text": "Ask each clinic what its quote includes."}, _ctx(tid))
    pending = await reg.execute("marketing.request_content_approval", {"content_id": cid}, _ctx(tid))
    assert pending.content["status"] == "PENDING_APPROVAL"
    listed = (await reg.execute("marketing.list_content", {}, _ctx(tid))).content
    assert [(c["title"], c["status"]) for c in listed] == [("How to compare clinics", "PENDING_APPROVAL")]


async def test_one_business_never_sees_another_businesss_content(client) -> None:
    a = await _register(client, "Content A Co", "contenta@example.com")
    b = await _register(client, "Content B Co", "contentb@example.com")
    a_tid, b_tid = await _tenant_id(client, a), await _tenant_id(client, b)
    await get_tool_registry().execute("marketing.create_content_idea", {"title": "Private idea"}, _ctx(a_tid))
    assert (await get_tool_registry().execute("marketing.list_content", {}, _ctx(b_tid))).content == []


async def test_growth_scout_stops_cleanly_when_there_are_no_campaigns() -> None:
    scout = _member("growth-scout")
    assert scout.tools[0] == "marketing.list_campaigns"
    assert "if there are none, do not call any other tool" in scout.instructions
    registry = get_tool_registry()
    for tool in scout.tools:
        registry.get(tool)
