"""Real WebSocket connectivity test of the Media Streams route
(app/api/v1/voice_stream.py) — a genuine WebSocket connection to our own
app, sending real Twilio-shaped protocol frames.

Scope note: this only exercises the transport/protocol layer without a
resolvable tenant (no DB-backed CallSession lookup happens on this path),
because `starlette.testclient.TestClient`'s WebSocket support runs the
app's lifespan in a separate thread/portal from this project's
pytest-asyncio-managed database fixtures (see tests/conftest.py's
`_reset_database` autouse fixture) — reconciling the two safely was out of
scope for this pass. The DB-backed conversation/governance logic this
route calls into is already covered for real by
tests/test_voice_call_service.py and tests/test_voice_conversation_service.py;
this file only proves the socket itself accepts real protocol traffic
and never crashes or hangs on it.
"""

import json

from starlette.testclient import TestClient

from app.main import app


def test_media_stream_accepts_connection_and_handles_full_frame_sequence() -> None:
    with TestClient(app) as test_client:
        with test_client.websocket_connect("/api/v1/voice-stream") as ws:
            ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": "MZ1", "callSid": "CA-no-tenant"},
            }))
            ws.send_text(json.dumps({
                "event": "media",
                "media": {"track": "inbound", "chunk": "1", "timestamp": "1", "payload": "aGVsbG8="},
            }))
            ws.send_text(json.dumps({"event": "stop", "stop": {}}))
    # No assertion beyond "did not raise / did not hang" — a `start`
    # event with no tenant_id/call_session_id customParameters has no
    # tenant to touch, and the route must end cleanly rather than guess.


def test_media_stream_survives_malformed_and_unknown_frames() -> None:
    with TestClient(app) as test_client:
        with test_client.websocket_connect("/api/v1/voice-stream") as ws:
            ws.send_text("not valid json at all {{{")
            ws.send_text(json.dumps({"event": "totally-unknown-event-type"}))
            ws.send_text(json.dumps({"event": "media", "media": {"payload": "not-valid-base64!!!"}}))
            ws.send_text(json.dumps({"event": "stop", "stop": {}}))
    # Must not raise, hang, or crash the ASGI app — malformed/unknown
    # frames are logged and skipped, never fatal.


def test_media_stream_closes_cleanly_on_immediate_disconnect() -> None:
    with TestClient(app) as test_client:
        with test_client.websocket_connect("/api/v1/voice-stream") as ws:
            ws.close()
