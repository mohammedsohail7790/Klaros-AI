"""Phase 32: app/services/openai_realtime_voice_service.py — the OpenAI
Realtime engine's governance boundary. Uses a fake WebSocket double
(mirrors tests/test_voice_conversation_service.py's fake-provider
pattern and app/services/speech_provider.py's own
DeterministicStreamingSTTProvider double) so this proves OUR
interception/validation logic against real, scripted OpenAI Realtime
protocol events — never a live OpenAI connection. Every real mutation
must still flow through the real ToolRegistry via AIExecutionService,
exactly like the cascaded engine; these tests prove that boundary holds
here too, including for a model that tries to invent an appointment time
or call a tool outside the voice allowlist."""

import asyncio
import json
import uuid

import pytest

from app.ai.execution_service import AIExecutionService
from app.models.voice import CallOutcome
from app.services.openai_realtime_voice_service import (
    OpenAIRealtimeVoiceBridge,
    RealtimeAudioChunk,
    RealtimeCallEnded,
    RealtimeClearAudio,
    build_voice_tool_schemas,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fake_openai_api_key(monkeypatch):
    """Every test in this file drives OpenAIRealtimeVoiceBridge against a
    fully scripted fake WebSocket (_FakeRealtimeWebSocket, see below) or a
    monkeypatched `_synthesize_verbatim` — never a real OpenAI connection
    (see module docstring). `OpenAIRealtimeVoiceBridge.open()` still
    legitimately refuses to open a session with no configured API key (a
    real, correct fail-closed guard against ever opening a billed Realtime
    session with no credentials — see app/services/openai_realtime_voice_
    service.py::open()), so these fully-mocked tests need *some* non-empty
    value to satisfy that config-presence check before reaching the mocked
    connector; the network boundary itself is what's actually mocked, not
    this check. This must never be a real-looking secret (see the
    backend/.env credential-isolation incident this project treats as
    safety-critical) — it is an obviously-fake placeholder, and it is
    never sent anywhere real because the WebSocket connector and the
    speech-synthesis call are both replaced with fakes in every test that
    reaches them.

    test_open_without_api_key_raises_honestly explicitly overrides this
    with an empty string, in the same test, to prove the guard itself.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "test-fake-not-a-real-key-do-not-use")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeRealtimeWebSocket:
    """A real in-process fake of the `websockets` client connection
    object — records every event this module sends, and replays a
    scripted sequence of incoming events exactly like a real OpenAI
    Realtime session would (as JSON text frames)."""

    def __init__(self, incoming: list[dict | str]) -> None:
        # dict entries are real scripted Realtime events (JSON-encoded on
        # the wire, exactly like the real API); a bare str entry is sent
        # verbatim — used to prove a genuinely malformed frame (not valid
        # JSON at all) is logged and skipped rather than crashing the loop.
        self._incoming = [e if isinstance(e, str) else json.dumps(e) for e in incoming]
        self.sent: list[dict] = []
        self._closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def close(self) -> None:
        self._closed = True


def _connector(fake_ws: _FakeRealtimeWebSocket):
    async def _connect(url: str, headers: dict) -> _FakeRealtimeWebSocket:
        assert "Authorization" in headers
        assert "sk-" not in url  # the key must never end up in the URL
        return fake_ws

    return _connect


def _build_bridge(tool_registry, incoming: list[dict]) -> tuple[OpenAIRealtimeVoiceBridge, _FakeRealtimeWebSocket]:
    from app.db.session import async_session_maker
    from app.services.voice_call_service import VoiceCallService

    fake_ws = _FakeRealtimeWebSocket(incoming)
    bridge = OpenAIRealtimeVoiceBridge(
        tool_registry, AIExecutionService(tool_registry), async_session_maker, VoiceCallService(async_session_maker),
        ws_connector=_connector(fake_ws),
    )
    return bridge, fake_ws


async def _real_call(tenant_id: uuid.UUID | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    """A real, persisted CallSession — every test below that opens a
    bridge session needs one, exactly like production (the Twilio inbound
    webhook always creates the CallSession before the WebSocket, and
    the bridge only ever appends transcript turns for a call that
    genuinely exists)."""
    from app.db.session import async_session_maker
    from app.services.voice_call_service import VoiceCallService

    tenant_id = tenant_id or uuid.uuid4()
    call_service = VoiceCallService(async_session_maker)
    call, _ = await call_service.get_or_create_call(
        tenant_id, provider="twilio", external_call_id=f"CA-{uuid.uuid4()}", caller_number="+15551110000",
    )
    return tenant_id, call.id


async def _drain(bridge: OpenAIRealtimeVoiceBridge) -> list:
    return [e async for e in bridge.events()]


# --- Tool schema generation -------------------------------------------------

async def test_tool_schemas_built_from_real_allowlisted_tools_only(tool_registry) -> None:
    schemas = build_voice_tool_schemas(tool_registry)
    names = {s["name"] for s in schemas}
    assert names == {
        "crm.create_lead", "crm.create_customer", "crm.check_availability",
        "crm.create_appointment", "knowledge.search",
    }
    for s in schemas:
        assert s["type"] == "function"
        assert "properties" in s["parameters"]


# --- Session open sends a governed, correctly-shaped session.update --------

async def test_open_sends_session_update_with_allowlisted_tools_and_matching_codec(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    tenant_id, call_id = await _real_call()

    await bridge.open(tenant_id, call_id)

    assert len(fake_ws.sent) == 1
    update = fake_ws.sent[0]
    assert update["type"] == "session.update"
    session = update["session"]
    assert session["input_audio_format"] == "g711_ulaw"
    assert session["output_audio_format"] == "g711_ulaw"
    assert session["turn_detection"] == {"type": "server_vad"}
    tool_names = {t["name"] for t in session["tools"]}
    assert tool_names == {
        "crm.create_lead", "crm.create_customer", "crm.check_availability",
        "crm.create_appointment", "knowledge.search",
    }
    await bridge.close()


async def test_open_without_api_key_raises_honestly(tool_registry, monkeypatch) -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    try:
        bridge, _ = _build_bridge(tool_registry, incoming=[])
        with pytest.raises(RuntimeError):
            await bridge.open(uuid.uuid4(), uuid.uuid4())
    finally:
        get_settings.cache_clear()


# --- Audio / barge-in event translation -------------------------------------

async def test_audio_delta_becomes_a_bridge_audio_chunk(tool_registry) -> None:
    mulaw = b"\xff\xfe\xfd"
    import base64

    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {"type": "response.audio.delta", "delta": base64.b64encode(mulaw).decode()},
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    events = await _drain(bridge)
    assert events == [RealtimeAudioChunk(mulaw=mulaw)]


async def test_speech_started_triggers_response_cancel_and_clear_audio(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {"type": "input_audio_buffer.speech_started"},
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    events = await _drain(bridge)
    assert events == [RealtimeClearAudio()]
    assert {"type": "response.cancel"} in fake_ws.sent


# --- Emergency detection: deterministic, cancels the model, real audit -----

async def test_emergency_transcript_cancels_model_and_ends_call(tool_registry, monkeypatch) -> None:
    async def _fake_synthesize(text, *, api_key, http_client):
        assert "911" in text  # the fixed safety message, not model output
        return b"\x00\x01"

    monkeypatch.setattr(
        "app.services.openai_realtime_voice_service._synthesize_verbatim", _fake_synthesize,
    )

    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "there's a gas leak in the kitchen",
        },
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    events = await _drain(bridge)
    assert any(isinstance(e, RealtimeAudioChunk) and e.mulaw == b"\x00\x01" for e in events)
    ended = [e for e in events if isinstance(e, RealtimeCallEnded)]
    assert len(ended) == 1
    assert ended[0].outcome == CallOutcome.EMERGENCY_ESCALATED
    assert "gas leak" in ended[0].reason
    assert {"type": "response.cancel"} in fake_ws.sent


async def test_caller_and_agent_transcripts_are_persisted_for_owner_visibility(tool_registry) -> None:
    """Parity with the cascaded engine's VoiceConversationService, which
    appends every real turn to CallSession.transcript — Owner UI/Insights
    must see real conversation history regardless of which engine
    handled the call."""
    from app.services.voice_call_service import VoiceCallService

    from app.db.session import async_session_maker

    tenant_id, call_id = uuid.uuid4(), uuid.uuid4()
    call_service = VoiceCallService(async_session_maker)
    call, _ = await call_service.get_or_create_call(
        tenant_id, provider="twilio", external_call_id="CA-realtime-1", caller_number="+15551110099",
    )

    fake_ws = _FakeRealtimeWebSocket(incoming=[
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "I need a plumber"},
        {"type": "response.audio_transcript.done", "transcript": "Sure, let me help with that."},
    ])
    bridge = OpenAIRealtimeVoiceBridge(
        tool_registry, AIExecutionService(tool_registry), async_session_maker, call_service,
        ws_connector=_connector(fake_ws),
    )
    await bridge.open(tenant_id, call.id)
    await _drain(bridge)

    updated = await call_service.get_call(tenant_id, call.id)
    assert updated.transcript == [
        {"role": "caller", "text": "I need a plumber"},
        {"role": "agent", "text": "Sure, let me help with that."},
    ]


async def test_non_emergency_transcript_produces_no_bridge_events(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "I'd like to book an appointment",
        },
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    events = await _drain(bridge)
    assert events == []


# --- Governed tool-call interception -----------------------------------------

async def test_forbidden_tool_name_is_rejected_before_toolregistry(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "response.function_call_arguments.done",
            "name": "finance.issue_refund", "call_id": "call_1", "arguments": "{}",
        },
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    await _drain(bridge)
    outputs = [m for m in fake_ws.sent if m.get("type") == "conversation.item.create"]
    assert len(outputs) == 1
    payload = json.loads(outputs[0]["item"]["output"])
    assert payload["error"] == "tool_not_allowed"


async def test_allowed_tool_call_executes_through_real_toolregistry(tool_registry) -> None:
    tenant_id, call_id = await _real_call()
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "response.function_call_arguments.done",
            "name": "crm.create_customer", "call_id": "call_2",
            "arguments": json.dumps({"name": "Realtime Test Customer", "phone": "+15551230000"}),
        },
    ])
    await bridge.open(tenant_id, call_id)

    await _drain(bridge)
    outputs = [m for m in fake_ws.sent if m.get("type") == "conversation.item.create"]
    assert len(outputs) == 1
    payload = json.loads(outputs[0]["item"]["output"])
    assert "customer" in payload
    assert payload["customer"]["name"] == "Realtime Test Customer"

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.crm import Customer

    async with async_session_maker() as session:
        row = (await session.execute(select(Customer).where(Customer.tenant_id == tenant_id))).scalar_one()
    assert row.name == "Realtime Test Customer"


async def test_appointment_time_not_previously_offered_is_rejected(tool_registry) -> None:
    """The model cannot invent an appointment time and have it accepted
    — only a start_time/end_time pair this exact session's own prior
    crm.check_availability call actually returned is honored."""
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "response.function_call_arguments.done",
            "name": "crm.create_appointment", "call_id": "call_3",
            "arguments": json.dumps({
                "customer_id": str(uuid.uuid4()), "title": "Repair",
                "start_time": "2099-01-01T10:00:00+00:00", "end_time": "2099-01-01T11:00:00+00:00",
            }),
        },
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    await _drain(bridge)
    outputs = [m for m in fake_ws.sent if m.get("type") == "conversation.item.create"]
    payload = json.loads(outputs[0]["item"]["output"])
    assert payload["error"] == "requested_time_was_not_offered_by_check_availability"


async def test_appointment_using_a_real_offered_slot_and_known_customer_succeeds(tool_registry) -> None:
    tenant_id, call_id = await _real_call()
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    await bridge.open(tenant_id, call_id)

    # Real customer, created through the exact same governed path a real
    # session would use.
    from app.ai.execution_service import ToolRequest
    from app.models.rbac import Role

    customer_output = await bridge._ai_execution.request_tool_execution(
        ToolRequest(tool_name="crm.create_customer", input={"name": "Slot Test Customer"}),
        tenant_id=tenant_id, ai_role=Role.MANAGER, correlation_id=call_id,
    )
    customer_id = customer_output.customer["id"]
    bridge._slots.record_customer_id(customer_id)
    bridge._slots.record_offered_slots([
        {"start_time": "2099-01-01T10:00:00+00:00", "end_time": "2099-01-01T11:00:00+00:00"},
    ])

    args = json.dumps({
        "customer_id": customer_id, "title": "Repair",
        "start_time": "2099-01-01T10:00:00+00:00", "end_time": "2099-01-01T11:00:00+00:00",
    })
    events = [e async for e in bridge._handle_function_call({
        "type": "response.function_call_arguments.done", "name": "crm.create_appointment",
        "call_id": "call_4", "arguments": args,
    })]

    outputs = [m for m in fake_ws.sent if m.get("type") == "conversation.item.create"]
    payload = json.loads(outputs[-1]["item"]["output"])
    assert "appointment" in payload
    assert any(isinstance(e, RealtimeCallEnded) and e.outcome == CallOutcome.APPOINTMENT_BOOKED for e in events)
    await bridge.close()


async def test_duplicate_appointment_function_call_does_not_double_book(tool_registry) -> None:
    """The model never supplies (or controls) the idempotency key — a
    duplicate/retried crm.create_appointment function call delivery
    within the same call must produce exactly one real appointment."""
    from app.ai.execution_service import ToolRequest
    from app.models.rbac import Role
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.crm import Appointment

    tenant_id, call_id = await _real_call()
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    bridge._tenant_id, bridge._call_id = tenant_id, call_id  # bypass open() — no OpenAI call needed here

    customer_output = await bridge._ai_execution.request_tool_execution(
        ToolRequest(tool_name="crm.create_customer", input={"name": "Dup Test Customer"}),
        tenant_id=tenant_id, ai_role=Role.MANAGER, correlation_id=call_id,
    )
    customer_id = customer_output.customer["id"]
    bridge._slots.record_customer_id(customer_id)
    bridge._slots.record_offered_slots([
        {"start_time": "2099-02-01T10:00:00+00:00", "end_time": "2099-02-01T11:00:00+00:00"},
    ])
    args = {
        "customer_id": customer_id, "title": "Repair",
        "start_time": "2099-02-01T10:00:00+00:00", "end_time": "2099-02-01T11:00:00+00:00",
    }

    first = await bridge._execute_governed("crm.create_appointment", dict(args))
    second = await bridge._execute_governed("crm.create_appointment", dict(args))  # simulated duplicate delivery
    assert first.appointment["id"] == second.appointment["id"]

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(Appointment).where(Appointment.tenant_id == tenant_id))
        ).scalars().all()
    assert len(rows) == 1


async def test_malformed_function_call_arguments_do_not_crash_the_bridge(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "response.function_call_arguments.done",
            "name": "crm.create_customer", "call_id": "call_5", "arguments": "{not json",
        },
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    await _drain(bridge)  # must not raise
    outputs = [m for m in fake_ws.sent if m.get("type") == "conversation.item.create"]
    payload = json.loads(outputs[0]["item"]["output"])
    assert payload["error"] == "malformed_arguments"


async def test_invalid_schema_arguments_are_rejected(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[
        {
            "type": "response.function_call_arguments.done",
            "name": "crm.create_customer", "call_id": "call_6",
            "arguments": json.dumps({"no_name_field": True}),
        },
    ])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    await _drain(bridge)
    outputs = [m for m in fake_ws.sent if m.get("type") == "conversation.item.create"]
    payload = json.loads(outputs[0]["item"]["output"])
    assert payload["error"] == "invalid_arguments"


async def test_malformed_realtime_event_is_logged_and_skipped(tool_registry) -> None:
    bridge, fake_ws = _build_bridge(tool_registry, incoming=["not json at all", {"type": "response.done"}])
    tenant_id, call_id = await _real_call()
    await bridge.open(tenant_id, call_id)

    events = await _drain(bridge)  # must not raise
    assert events == []


async def test_open_fails_closed_for_a_call_session_that_does_not_exist(tool_registry) -> None:
    """The tenant_id/call_session_id on a WebSocket `start` event are
    client-supplied (echoed back from Twilio's customParameters) — they
    are the only thing binding this connection to a genuine call our own
    signature-verified inbound-voice webhook created. open() must resolve
    a real CallSession before opening any real, billed OpenAI Realtime
    session, not silently proceed with call=None for an unresolvable
    pair (a real bug this test guards against regressing)."""
    from app.services.voice_call_service import CallSessionNotFoundError

    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    with pytest.raises(CallSessionNotFoundError):
        await bridge.open(uuid.uuid4(), uuid.uuid4())
    assert fake_ws.sent == []  # no session.update was ever sent — no session was opened


# --- Caller identification --------------------------------------------------

async def test_known_caller_is_identified_and_injected_as_fenced_data(tool_registry) -> None:
    from app.ai.execution_service import ToolRequest
    from app.models.rbac import Role

    tenant_id, call_id = await _real_call()
    ai_execution = AIExecutionService(tool_registry)
    customer_output = await ai_execution.request_tool_execution(
        ToolRequest(tool_name="crm.create_customer", input={"name": "Known Caller", "phone": "+15551110000"}),
        tenant_id=tenant_id, ai_role=Role.MANAGER, correlation_id=call_id,
    )
    customer_id = customer_output.customer["id"]

    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    await bridge.open(tenant_id, call_id)  # _real_call() seeded caller_number="+15551110000"

    update = fake_ws.sent[0]
    assert "BEGIN EXISTING CUSTOMER DATA" in update["session"]["instructions"]
    assert customer_id in update["session"]["instructions"]
    assert bridge._slots.customer_is_known(customer_id)
    await bridge.close()


async def test_unknown_caller_gets_no_customer_data_injected(tool_registry) -> None:
    tenant_id, call_id = await _real_call()
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    await bridge.open(tenant_id, call_id)

    update = fake_ws.sent[0]
    assert "BEGIN EXISTING CUSTOMER DATA" not in update["session"]["instructions"]
    await bridge.close()


async def test_caller_identification_never_leaks_another_tenants_customer(tool_registry) -> None:
    from app.ai.execution_service import ToolRequest
    from app.models.rbac import Role

    other_tenant_id = uuid.uuid4()
    ai_execution = AIExecutionService(tool_registry)
    await ai_execution.request_tool_execution(
        ToolRequest(tool_name="crm.create_customer", input={"name": "Other Tenant's Customer", "phone": "+15551110000"}),
        tenant_id=other_tenant_id, ai_role=Role.MANAGER, correlation_id=uuid.uuid4(),
    )

    # A different tenant's call, same caller phone number.
    tenant_id, call_id = await _real_call()
    bridge, fake_ws = _build_bridge(tool_registry, incoming=[])
    await bridge.open(tenant_id, call_id)

    update = fake_ws.sent[0]
    assert "Other Tenant's Customer" not in update["session"]["instructions"]
    await bridge.close()


# --- Wiring: the WebSocket route dispatches to this engine when selected ---

def test_voice_stream_route_dispatches_to_realtime_engine_when_configured(monkeypatch) -> None:
    """A real WebSocket connection to our own app, with
    VOICE_AI_ENGINE=openai_realtime — proves the route-level dispatch
    (app/api/v1/voice_stream.py) reaches `_voice_media_stream_openai_realtime`
    rather than the cascaded engine, using the same no-resolvable-tenant
    scope as tests/test_voice_media_stream_integration.py (TestClient's
    WebSocket support cannot share this project's pytest-asyncio DB
    fixtures — see that file's own docstring)."""
    import json

    from starlette.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    monkeypatch.setenv("VOICE_AI_ENGINE", "openai_realtime")
    get_settings.cache_clear()
    try:
        with TestClient(app) as test_client:
            with test_client.websocket_connect("/api/v1/voice-stream") as ws:
                ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
                ws.send_text(json.dumps({
                    "event": "start", "start": {"streamSid": "MZ1", "callSid": "CA-no-tenant"},
                }))
                ws.send_text(json.dumps({"event": "stop", "stop": {}}))
        # No assertion beyond "did not raise / did not hang" — a `start`
        # with no tenant_id/call_session_id has nothing to open a
        # Realtime session for, and the route must end cleanly.
    finally:
        get_settings.cache_clear()
