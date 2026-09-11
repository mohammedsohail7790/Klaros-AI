"""insights.get_voice_snapshot and its Morning Brief integration — real
data, zero fabrication when there's no call activity."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.services.voice_call_service import VoiceCallService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def test_voice_snapshot_is_zero_with_no_calls(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    result = await tool_registry.execute("insights.get_voice_snapshot", {}, _ctx(tenant_id))
    assert result.calls_today == 0
    assert result.new_leads_from_voice_today == 0
    assert result.human_handoffs_today == 0
    assert result.recent_calls == []


async def test_voice_snapshot_reflects_real_call_activity(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    from app.db.session import async_session_maker

    call_service = VoiceCallService(async_session_maker)
    call1, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA1", caller_number="+1555")
    await call_service.update_call(tenant_id, call1.id, lead_id=uuid.uuid4())
    call2, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA2", caller_number="+1556")
    await call_service.update_call(tenant_id, call2.id, handoff_requested=True)

    result = await tool_registry.execute("insights.get_voice_snapshot", {}, _ctx(tenant_id))
    assert result.calls_today == 2
    assert result.new_leads_from_voice_today == 1
    assert result.human_handoffs_today == 1
    assert len(result.recent_calls) == 2


async def test_voice_snapshot_never_crosses_tenants(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    from app.db.session import async_session_maker

    call_service = VoiceCallService(async_session_maker)
    await call_service.get_or_create_call(tenant_a, provider="twilio", external_call_id="CA-A", caller_number="+1555")

    result = await tool_registry.execute("insights.get_voice_snapshot", {}, _ctx(tenant_b))
    assert result.calls_today == 0


async def test_morning_brief_includes_voice_handoff_recommendation(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    from app.db.session import async_session_maker

    call_service = VoiceCallService(async_session_maker)
    call, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA1", caller_number="+1555")
    await call_service.update_call(tenant_id, call.id, handoff_requested=True)

    await tool_registry.execute("insights.generate_morning_brief", {}, _ctx(tenant_id))
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, _ctx(tenant_id))

    voice_recs = [r for r in latest.recommendations if "call these customers back" in r.next_action.lower()]
    assert voice_recs


async def test_morning_brief_shows_no_voice_activity_when_there_is_none(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    await tool_registry.execute("insights.generate_morning_brief", {}, _ctx(tenant_id))
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, _ctx(tenant_id))
    voice_insights = [i for i in latest.insights if "receptionist" in i.summary.lower() or "AI receptionist" in i.summary]
    assert voice_insights == []
