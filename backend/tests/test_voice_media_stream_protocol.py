"""Pure parsing tests for Twilio's real, documented Media Streams
WebSocket protocol (app/api/v1/voice_stream.py::parse_media_stream_event)
— no live socket, no live Twilio call; this tests OUR parser against
synthetic, protocol-shaped frames."""

import base64
import json
import uuid

import pytest

from app.api.v1.voice_stream import (
    MalformedMediaStreamEventError,
    MediaEvent,
    StartEvent,
    parse_media_stream_event,
)


def test_connected_event_parses() -> None:
    event, payload = parse_media_stream_event(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
    assert event == "connected"
    assert payload is None


def test_start_event_parses_customparameters() -> None:
    tenant_id = uuid.uuid4()
    call_id = uuid.uuid4()
    raw = json.dumps({
        "event": "start",
        "start": {
            "streamSid": "MZ123", "callSid": "CA123", "accountSid": "AC123",
            "customParameters": {"tenant_id": str(tenant_id), "call_session_id": str(call_id)},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
    })
    event, payload = parse_media_stream_event(raw)
    assert event == "start"
    assert isinstance(payload, StartEvent)
    assert payload.tenant_id == tenant_id
    assert payload.call_session_id == call_id
    assert payload.stream_sid == "MZ123"


def test_media_event_decodes_base64_payload() -> None:
    audio = b"\x00\x01\x02fake-mulaw-bytes"
    raw = json.dumps({
        "event": "media",
        "media": {"track": "inbound", "chunk": "1", "timestamp": "5", "payload": base64.b64encode(audio).decode()},
    })
    event, payload = parse_media_stream_event(raw)
    assert event == "media"
    assert isinstance(payload, MediaEvent)
    assert payload.payload == audio


def test_stop_event_parses() -> None:
    event, payload = parse_media_stream_event(json.dumps({"event": "stop", "stop": {"callSid": "CA123"}}))
    assert event == "stop"


def test_mark_event_parses() -> None:
    event, _payload = parse_media_stream_event(json.dumps({"event": "mark", "mark": {"name": "test"}}))
    assert event == "mark"


def test_invalid_json_raises_malformed_error() -> None:
    with pytest.raises(MalformedMediaStreamEventError):
        parse_media_stream_event("not json{{{")


def test_missing_event_field_raises() -> None:
    with pytest.raises(MalformedMediaStreamEventError):
        parse_media_stream_event(json.dumps({"foo": "bar"}))


def test_unknown_event_type_raises() -> None:
    with pytest.raises(MalformedMediaStreamEventError):
        parse_media_stream_event(json.dumps({"event": "totally-made-up"}))


def test_start_event_missing_stream_sid_raises() -> None:
    raw = json.dumps({"event": "start", "start": {"callSid": "CA123"}})
    with pytest.raises(MalformedMediaStreamEventError):
        parse_media_stream_event(raw)


def test_start_event_malformed_custom_parameters_raises() -> None:
    raw = json.dumps({
        "event": "start",
        "start": {"streamSid": "MZ1", "callSid": "CA1", "customParameters": {"tenant_id": "not-a-uuid"}},
    })
    with pytest.raises(MalformedMediaStreamEventError):
        parse_media_stream_event(raw)


def test_media_event_invalid_base64_raises() -> None:
    raw = json.dumps({"event": "media", "media": {"payload": "not-valid-base64!!!"}})
    with pytest.raises(MalformedMediaStreamEventError):
        parse_media_stream_event(raw)


def test_start_event_without_tenant_or_call_id_parses_with_none_fields() -> None:
    """A start event can be well-formed but simply lack customParameters
    (e.g. someone connects to the WS endpoint directly, not via our own
    TwiML) — the parser itself must not reject that; the route handler is
    responsible for refusing to proceed without them."""
    raw = json.dumps({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    event, payload = parse_media_stream_event(raw)
    assert event == "start"
    assert payload.tenant_id is None
    assert payload.call_session_id is None
