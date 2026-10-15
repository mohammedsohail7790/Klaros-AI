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


async def test_the_marketing_manager_reports_and_the_content_drafter_runs_one_tool_directly() -> None:
    registry = get_tool_registry()
    manager, drafter = _member("marketing-manager"), _member("content-drafter")
    for tool in (*manager.tools, *drafter.tools):
        registry.get(tool)
    assert "marketing.publish_content_variant" not in manager.tools + drafter.tools and "marketing.approve_content" not in manager.tools + drafter.tools
    assert DEFAULT_TOOL_POLICIES["marketing.list_content"] == ActionPolicy.AUTO and DEFAULT_TOOL_POLICIES["marketing.draft_content"] == ActionPolicy.AUTO
    schedule = oc._triggers(drafter)["schedule"]
    assert schedule["action"] == {"tool_name": "marketing.draft_content", "tool_input": {"max_items": 2}} and schedule["time"] == "10:35"
    assert "action" not in oc._triggers(manager)["schedule"]


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


# ---- the deterministic content step ---------------------------------------------------------------------------------------------------------------------

from app.services.mt_content_topics import TOPICS_PATH, build_topics, parse_topics  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402


def test_topics_are_built_from_setup_and_contain_no_claims() -> None:
    md = build_topics("Aesthetic Tourism", "both", ["Dental", "Hair transplant"])
    topics = parse_topics(md)
    assert [t for t, _ in topics] == ["How Aesthetic Tourism works", "Questions to ask before choosing Dental abroad", "Questions to ask before choosing Hair transplant abroad", "Planning your trip around treatment"]
    assert all(len(b.split()) < 140 for _, b in topics)
    for _, body in topics:  # the sections themselves (not the file's own header, which tells the owner what NOT to add) carry no claim words
        for banned in ("guarantee", "%", "$", "best clinic", "cheapest", "success rate", "best in"):
            assert banned not in body.lower()
    assert parse_topics("no sections here") == []


async def test_draft_content_turns_topics_into_drafts_for_approval_once_and_within_limits(client) -> None:
    token = await _register(client, "Draft Co", "draftco@example.com")
    tid = await _tenant_id(client, token)
    reg = get_tool_registry()
    empty = await reg.execute("marketing.draft_content", {}, _ctx(tid))
    assert empty.created == [] and "no content topics" in empty.note
    await KnowledgeService(async_session_maker).set_file(tid, TOPICS_PATH, build_topics("Draft Co", "referral", ["Dental", "Hair transplant"]), actor_id=None)
    first = await reg.execute("marketing.draft_content", {"max_items": 2}, _ctx(tid))
    assert len(first.created) == 2 and all(c["status"] == "PENDING_APPROVAL" for c in first.created)
    again = await reg.execute("marketing.draft_content", {"max_items": 2}, _ctx(tid))
    assert len(again.created) == 1 and again.waiting_before == 2  # three may wait at once, never more
    third = await reg.execute("marketing.draft_content", {"max_items": 2}, _ctx(tid))
    assert third.created == [] and "already waiting" in third.note
    titles = [c["title"] for c in (await reg.execute("marketing.list_content", {}, _ctx(tid))).content]
    assert len(titles) == len(set(titles)) == 3  # no title twice


async def test_draft_content_never_leaks_between_businesses(client) -> None:
    a = await _register(client, "Draft A Co", "drafta@example.com")
    b = await _register(client, "Draft B Co", "draftb@example.com")
    a_tid, b_tid = await _tenant_id(client, a), await _tenant_id(client, b)
    await KnowledgeService(async_session_maker).set_file(a_tid, TOPICS_PATH, build_topics("A", "referral", ["Dental"]), actor_id=None)
    await get_tool_registry().execute("marketing.draft_content", {}, _ctx(a_tid))
    out = await get_tool_registry().execute("marketing.draft_content", {}, _ctx(b_tid))
    assert out.created == [] and (await get_tool_registry().execute("marketing.list_content", {}, _ctx(b_tid))).content == []
