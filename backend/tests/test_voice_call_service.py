"""app/services/voice_call_service.py — CallSession lifecycle,
idempotency, and VoiceReceptionistSettings."""

import uuid

import pytest

from app.models.voice import CallOutcome, CallStatus
from app.services.voice_call_service import CallSessionNotFoundError, VoiceCallService

pytestmark = pytest.mark.asyncio


def _service() -> VoiceCallService:
    from app.db.session import async_session_maker

    return VoiceCallService(async_session_maker)


async def test_settings_default_to_disabled() -> None:
    tenant_id = uuid.uuid4()
    settings = await _service().get_settings(tenant_id)
    assert settings.enabled is False
    assert settings.greeting


async def test_update_settings_persists() -> None:
    tenant_id = uuid.uuid4()
    service = _service()
    await service.update_settings(tenant_id, enabled=True, greeting="Hi there!")
    settings = await service.get_settings(tenant_id)
    assert settings.enabled is True
    assert settings.greeting == "Hi there!"


async def test_get_or_create_call_is_idempotent() -> None:
    tenant_id = uuid.uuid4()
    service = _service()
    call1, created1 = await service.get_or_create_call(
        tenant_id, provider="twilio", external_call_id="CA123", caller_number="+15551234567"
    )
    call2, created2 = await service.get_or_create_call(
        tenant_id, provider="twilio", external_call_id="CA123", caller_number="+15551234567"
    )
    assert created1 is True
    assert created2 is False
    assert call1.id == call2.id


async def test_calls_are_tenant_isolated() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = _service()
    await service.get_or_create_call(tenant_a, provider="twilio", external_call_id="CA-A", caller_number=None)
    await service.get_or_create_call(tenant_b, provider="twilio", external_call_id="CA-B", caller_number=None)

    calls_a = await service.list_calls(tenant_a)
    calls_b = await service.list_calls(tenant_b)
    assert len(calls_a) == 1
    assert len(calls_b) == 1
    assert calls_a[0].external_call_id == "CA-A"
    assert calls_b[0].external_call_id == "CA-B"


async def test_get_call_unknown_id_raises() -> None:
    with pytest.raises(CallSessionNotFoundError):
        await _service().get_call(uuid.uuid4(), uuid.uuid4())


async def test_get_call_never_returns_another_tenants_call() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = _service()
    call, _ = await service.get_or_create_call(tenant_a, provider="twilio", external_call_id="CA-X", caller_number=None)

    with pytest.raises(CallSessionNotFoundError):
        await service.get_call(tenant_b, call.id)


async def test_append_transcript_turn_accumulates_in_order() -> None:
    tenant_id = uuid.uuid4()
    service = _service()
    call, _ = await service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-T", caller_number=None)
    await service.append_transcript_turn(tenant_id, call.id, role="caller", text="Hi, I need a plumber")
    call = await service.append_transcript_turn(tenant_id, call.id, role="agent", text="Sure, what's going on?")
    assert call.transcript == [
        {"role": "caller", "text": "Hi, I need a plumber"},
        {"role": "agent", "text": "Sure, what's going on?"},
    ]


async def test_end_call_sets_outcome_and_status() -> None:
    tenant_id = uuid.uuid4()
    service = _service()
    call, _ = await service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-E", caller_number=None)
    ended = await service.end_call(tenant_id, call.id, outcome=CallOutcome.NEW_LEAD_CREATED)
    assert ended.status == CallStatus.COMPLETED
    assert ended.outcome == CallOutcome.NEW_LEAD_CREATED
    assert ended.ended_at is not None
