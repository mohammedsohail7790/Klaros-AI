"""workforce.call_lead: an agent can ask the AI receptionist to phone a lead, but only with a person's approval by default, never twice in a day, and never when the
receptionist is not connected. Halla is a recorded mock (see tests/test_halla_integration.py); no real call is placed."""

import uuid

import pytest

from app.api.tool_deps import get_tool_registry
from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_halla_integration import _connected, _lead, _register, _tenant_id, halla  # noqa: F401



def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


def test_a_call_needs_approval_by_default() -> None:
    assert DEFAULT_TOOL_POLICIES["workforce.call_lead"] == ActionPolicy.APPROVAL_REQUIRED
    get_tool_registry().get("workforce.call_lead")  # registered


@pytest.mark.asyncio
async def test_an_unapproved_call_places_nothing(client, halla) -> None:  # noqa: F811
    token, tid = await _connected(client, "Gate Call Co", "gatecall@example.com")
    lead_id = await _lead(client, token, phone="+971500000010")
    with pytest.raises(ToolApprovalRequiredError):
        await get_tool_registry().execute("workforce.call_lead", {"lead_id": lead_id, "reason": "qualification"}, _ctx(tid))
    assert halla.sent("POST", "/calls/outbound") == []  # waiting for a person, nothing dialled


@pytest.mark.asyncio
async def test_an_approved_call_is_placed_once_and_not_repeated_within_a_day(client, halla) -> None:  # noqa: F811
    token, tid = await _connected(client, "Once Call Co", "oncecall@example.com")
    lead_id = await _lead(client, token, phone="+971500000011")
    registry = get_tool_registry()
    first = await registry.execute("workforce.call_lead", {"lead_id": lead_id}, _ctx(tid), skip_approval_gate=True)
    assert first.requested is True and first.call_id == "CA123"
    second = await registry.execute("workforce.call_lead", {"lead_id": lead_id}, _ctx(tid), skip_approval_gate=True)
    assert second.requested is False and "24 hours" in second.skipped_reason
    assert len(halla.sent("POST", "/calls/outbound")) == 1


@pytest.mark.asyncio
async def test_a_lead_without_a_phone_or_without_a_connected_receptionist_is_skipped_not_failed(client, halla) -> None:  # noqa: F811
    token, tid = await _connected(client, "Skip Call Co", "skipcall@example.com")
    nophone = await _lead(client, token, name="No Phone", phone=None)
    out = await get_tool_registry().execute("workforce.call_lead", {"lead_id": nophone}, _ctx(tid), skip_approval_gate=True)
    assert out.requested is False and out.skipped_reason

    other = await _register(client, "Unconnected Co", "unconnected@example.com")
    other_tid = await _tenant_id(client, other)
    lead = await _lead(client, other, phone="+971500000012")
    out = await get_tool_registry().execute("workforce.call_lead", {"lead_id": lead}, _ctx(other_tid), skip_approval_gate=True)
    assert out.requested is False and out.skipped_reason
    assert halla.sent("POST", "/calls/outbound") == []


@pytest.mark.asyncio
async def test_a_business_can_let_its_crew_call_on_its_own_and_only_that_business(client, halla) -> None:  # noqa: F811
    from app.db.session import async_session_maker
    from app.services.policy_service import PolicyService

    token, tid = await _connected(client, "Auto Call Co", "autocall@example.com")
    lead_id = await _lead(client, token, phone="+971500000013")
    await PolicyService(async_session_maker).set_policy(tid, "workforce.call_lead", ActionPolicy.AUTO, actor_id=None)
    out = await get_tool_registry().execute("workforce.call_lead", {"lead_id": lead_id}, _ctx(tid))  # the tenant's own policy lets it through, no approval needed
    assert out.requested is True and len(halla.sent("POST", "/calls/outbound")) == 1

    other_token, other_tid = await _connected(client, "Approval Co", "approvalco@example.com")
    other_lead = await _lead(client, other_token, phone="+971500000014")
    with pytest.raises(ToolApprovalRequiredError):  # another business is unaffected
        await get_tool_registry().execute("workforce.call_lead", {"lead_id": other_lead}, _ctx(other_tid))
    assert len(halla.sent("POST", "/calls/outbound")) == 1


@pytest.mark.asyncio
async def test_a_skipped_call_does_not_use_up_the_allowance(client, halla) -> None:  # noqa: F811
    from app.integrations.workforce import halla_adapter  # noqa: F401

    token = await _register(client, "Skipped Call Co", "skippedcall@example.com")  # Halla is not connected for this business yet
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000015")
    reg = get_tool_registry()
    for _ in range(4):  # more skipped attempts than the three-call lifetime cap
        out = await reg.execute("workforce.call_lead", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)
        assert out.requested is False and "maximum" not in (out.skipped_reason or "") and "24 hours" not in (out.skipped_reason or "")
    from tests.test_halla_integration import _connect
    assert (await _connect(client, token)).status_code == 200  # now Halla is connected
    first = await reg.execute("workforce.call_lead", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)
    assert first.requested is True  # the earlier skipped attempts did not block the first real call
