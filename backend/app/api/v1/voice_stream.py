"""Phase 4/6: the Twilio Media Streams WebSocket endpoint — real,
publicly-documented protocol handling (connected/start/media/stop/mark
events; see https://www.twilio.com/docs/voice/media-streams/websocket-
messages), reused/extended nowhere else in this codebase.

Phase 6 wires this into the real-time turn manager
(app/services/voice_realtime_service.py): every inbound `media` frame is
fed to the configured StreamingSTTProvider and scored for silence in real
time; an utterance boundary (speech followed by a short silence gap)
triggers a real conversation turn through the existing, UNCHANGED
VoiceConversationService/Phase 5 booking state machine, and the reply is
streamed back to Twilio as real `media` events (with a `clear` event on
caller barge-in). This has never been exercised against a live Twilio
call with real audio, and no reachable Deepgram/ElevenLabs streaming
session exists in this sandbox — the protocol handling, silence/barge-in
policy, and turn orchestration are real and tested (see
tests/test_voice_realtime_service.py); the live audio round-trip is
IMPLEMENTED, NOT LIVE-VERIFIED. See ARCHITECTURE_TRACEABILITY.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.tool_deps import (
    get_openai_realtime_voice_bridge,
    get_tool_registry,
    get_voice_call_service,
    get_voice_conversation_service,
)
from app.core.config import get_settings
from app.models.voice import CallOutcome
from app.services.audio_codec import pcm16_bytes_to_mulaw
from app.services.openai_realtime_voice_service import RealtimeAudioChunk, RealtimeCallEnded, RealtimeClearAudio
from app.services.speech_provider import get_streaming_stt_provider, get_streaming_tts_provider
from app.services.voice_realtime_service import RealtimeCallState, RealtimeTurnManager, SilenceAction

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["voice-stream"])


class MalformedMediaStreamEventError(Exception):
    pass


@dataclass
class StartEvent:
    stream_sid: str
    call_sid: str
    tenant_id: uuid.UUID | None
    call_session_id: uuid.UUID | None


@dataclass
class MediaEvent:
    payload: bytes


def parse_media_stream_event(raw: str) -> tuple[str, Any]:
    """Pure parser, tested independently of any live socket — returns
    (event_name, parsed_payload). Raises MalformedMediaStreamEventError on
    anything that isn't a well-formed Twilio Media Streams frame, never
    silently accepts garbage."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedMediaStreamEventError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or "event" not in data:
        raise MalformedMediaStreamEventError("missing 'event' field")

    event = data["event"]
    if event == "connected":
        return event, None
    if event == "start":
        start = data.get("start")
        if not isinstance(start, dict) or "streamSid" not in start or "callSid" not in start:
            raise MalformedMediaStreamEventError("malformed 'start' event")
        custom_params = start.get("customParameters") or {}
        tenant_id = None
        call_session_id = None
        try:
            if custom_params.get("tenant_id"):
                tenant_id = uuid.UUID(custom_params["tenant_id"])
            if custom_params.get("call_session_id"):
                call_session_id = uuid.UUID(custom_params["call_session_id"])
        except ValueError as exc:
            raise MalformedMediaStreamEventError(f"malformed customParameters: {exc}") from exc
        return event, StartEvent(
            stream_sid=start["streamSid"], call_sid=start["callSid"],
            tenant_id=tenant_id, call_session_id=call_session_id,
        )
    if event == "media":
        media = data.get("media")
        if not isinstance(media, dict) or "payload" not in media:
            raise MalformedMediaStreamEventError("malformed 'media' event")
        try:
            audio = base64.b64decode(media["payload"], validate=True)
        except Exception as exc:  # noqa: BLE001 — any decode failure is a malformed packet, not a crash
            raise MalformedMediaStreamEventError(f"invalid base64 media payload: {exc}") from exc
        return event, MediaEvent(payload=audio)
    if event in ("stop", "mark"):
        return event, data.get(event)

    raise MalformedMediaStreamEventError(f"unknown event type: {event!r}")


_PROMPT_TEXTS = {
    "greeting": "Hello, thanks for calling. How can I help you today?",
    "still_there": "Are you still there?",
    "hangup": "I haven't heard anything in a while, so I'll let you go. Feel free to call back anytime.",
}


@router.websocket("/voice-stream")
async def voice_media_stream(websocket: WebSocket) -> None:
    settings = get_settings()
    await websocket.accept()

    if settings.VOICE_AI_ENGINE == "openai_realtime":
        await _voice_media_stream_openai_realtime(websocket)
        return

    call_service = get_voice_call_service()
    conversation_service = get_voice_conversation_service(get_tool_registry())
    stt = get_streaming_stt_provider()
    tts = get_streaming_tts_provider()
    turn_manager = RealtimeTurnManager(call_service, conversation_service, stt, tts)

    start_info: StartEvent | None = None
    state: RealtimeCallState | None = None
    final_outcome: str | None = None

    async def _speak(text: str) -> None:
        if state is None:
            return
        async for pcm_chunk in turn_manager.stream_reply_audio(state, text):
            if state.barge_in.interrupt_requested:
                await websocket.send_text(json.dumps({"event": "clear", "streamSid": start_info.stream_sid}))
                break
            mulaw_chunk = pcm16_bytes_to_mulaw(pcm_chunk)
            await websocket.send_text(json.dumps({
                "event": "media", "streamSid": start_info.stream_sid,
                "media": {"payload": base64.b64encode(mulaw_chunk).decode()},
            }))

    async def _process_utterance() -> None:
        nonlocal final_outcome
        if state is None:
            return
        try:
            await stt.flush()
            transcript = await stt.receive_transcript()
        except Exception as exc:  # noqa: BLE001 — a real provider disconnect must not crash the call
            logger.error("voice_stream_stt_failed", error=str(exc))
            await call_service.end_call(
                state.tenant_id, state.call_id, outcome=CallOutcome.PROVIDER_FAILURE, failure_reason=f"stt_failed: {exc}"
            )
            final_outcome = CallOutcome.PROVIDER_FAILURE
            return
        if transcript is None or not transcript.text.strip():
            return

        turn_result = await turn_manager.run_conversation_turn(state, transcript.text)
        await _speak(turn_result.reply_text)
        if turn_result.outcome:
            final_outcome = turn_result.outcome
        if turn_result.call_ended:
            await call_service.end_call(state.tenant_id, state.call_id, outcome=turn_result.outcome or CallOutcome.UNRESOLVED)

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=settings.VOICE_MEDIA_STREAM_IDLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning("voice_stream_idle_timeout", stream_sid=start_info.stream_sid if start_info else None)
                break

            try:
                event, payload = parse_media_stream_event(raw)
            except MalformedMediaStreamEventError as exc:
                logger.warning("voice_stream_malformed_event", error=str(exc))
                continue

            if event == "connected":
                continue

            if event == "start":
                start_info = payload
                if start_info.tenant_id is None or start_info.call_session_id is None:
                    logger.error("voice_stream_missing_tenant_or_call_id", call_sid=start_info.call_sid)
                    break
                logger.info(
                    "voice_stream_started", tenant_id=str(start_info.tenant_id),
                    call_session_id=str(start_info.call_session_id), stream_sid=start_info.stream_sid,
                )
                state = RealtimeCallState(tenant_id=start_info.tenant_id, call_id=start_info.call_session_id)
                if stt.is_connected:
                    await stt.connect()
                continue

            if event == "media":
                if state is None:
                    continue
                if not stt.is_connected:
                    # Honest, immediate failure — never buffer audio a
                    # provider was never configured to transcribe.
                    await call_service.end_call(
                        state.tenant_id, state.call_id, outcome=CallOutcome.PROVIDER_FAILURE,
                        failure_reason="stt_not_configured",
                    )
                    final_outcome = CallOutcome.PROVIDER_FAILURE
                    break
                action = await turn_manager.handle_media_frame(state, payload.payload)
                if action == SilenceAction.UTTERANCE_END:
                    await _process_utterance()
                elif action == SilenceAction.PROMPT:
                    await _speak(_PROMPT_TEXTS["still_there"])
                elif action == SilenceAction.HANGUP:
                    await _speak(_PROMPT_TEXTS["hangup"])
                    await call_service.end_call(state.tenant_id, state.call_id, outcome=CallOutcome.UNRESOLVED, failure_reason="silence_timeout")
                    final_outcome = CallOutcome.UNRESOLVED
                    break
                continue

            if event == "stop":
                break

        if state is not None and final_outcome is None:
            # Loop ended (stop event / idle timeout) with no turn ever
            # having reached a terminal outcome — honestly UNRESOLVED,
            # never silently left ambiguous.
            await call_service.end_call(state.tenant_id, state.call_id, outcome=CallOutcome.UNRESOLVED)

    except WebSocketDisconnect:
        if state is not None and final_outcome is None:
            logger.info(
                "voice_stream_disconnected", tenant_id=str(state.tenant_id), call_session_id=str(state.call_id),
            )
            await call_service.end_call(state.tenant_id, state.call_id, outcome=CallOutcome.UNRESOLVED, failure_reason="caller_disconnected")


async def _voice_media_stream_openai_realtime(websocket: WebSocket) -> None:
    """Phase 32: the OpenAI Realtime engine's own loop — deliberately
    separate from the cascaded loop above rather than interleaved with
    it, so the existing, tested cascaded path is provably untouched (see
    ARCHITECTURE_TRACEABILITY.md's Phase 32 section). Twilio protocol
    parsing (`parse_media_stream_event`) and the tenant/call-session
    resolution contract (signed `<Parameter>`s from the inbound-voice
    webhook, echoed back on the `start` event) are identical to the
    cascaded path — only what happens to the audio/governance in between
    differs, and that lives entirely in
    app/services/openai_realtime_voice_service.py, never here."""
    settings = get_settings()
    call_service = get_voice_call_service()
    bridge = get_openai_realtime_voice_bridge(get_tool_registry())

    start_info: StartEvent | None = None
    final_outcome: str | None = None
    bridge_opened = False

    async def _drain_bridge_events() -> None:
        nonlocal final_outcome
        async for event in bridge.events():
            if isinstance(event, RealtimeAudioChunk):
                await websocket.send_text(json.dumps({
                    "event": "media", "streamSid": start_info.stream_sid,
                    "media": {"payload": base64.b64encode(event.mulaw).decode()},
                }))
            elif isinstance(event, RealtimeClearAudio):
                await websocket.send_text(json.dumps({"event": "clear", "streamSid": start_info.stream_sid}))
            elif isinstance(event, RealtimeCallEnded):
                final_outcome = event.outcome
                await call_service.end_call(
                    start_info.tenant_id, start_info.call_session_id,
                    outcome=event.outcome, failure_reason=event.reason,
                )
                return

    drain_task: asyncio.Task | None = None
    try:
        while True:
            receive_task = asyncio.ensure_future(websocket.receive_text())
            waitables = {receive_task}
            if drain_task is not None:
                waitables.add(drain_task)
            done, pending = await asyncio.wait(
                waitables, timeout=settings.VOICE_MEDIA_STREAM_IDLE_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED,
            )

            if drain_task is not None and drain_task in done:
                # The bridge already ended the call (emergency, appointment
                # booked, provider error) — stop reading Twilio frames and
                # let the WebSocket close, which is Twilio's documented
                # signal to end the call leg.
                if not receive_task.done():
                    receive_task.cancel()
                break

            if receive_task not in done:
                receive_task.cancel()
                logger.warning("voice_realtime_idle_timeout", stream_sid=start_info.stream_sid if start_info else None)
                break

            try:
                raw = receive_task.result()
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001 — a broken receive must not crash the whole handler
                logger.warning("voice_realtime_receive_failed", error=str(exc))
                break

            try:
                event, payload = parse_media_stream_event(raw)
            except MalformedMediaStreamEventError as exc:
                logger.warning("voice_realtime_malformed_event", error=str(exc))
                continue

            if event == "connected":
                continue

            if event == "start":
                start_info = payload
                if start_info.tenant_id is None or start_info.call_session_id is None:
                    logger.error("voice_realtime_missing_tenant_or_call_id", call_sid=start_info.call_sid)
                    break
                logger.info(
                    "voice_realtime_started", tenant_id=str(start_info.tenant_id),
                    call_session_id=str(start_info.call_session_id), stream_sid=start_info.stream_sid,
                )
                try:
                    await bridge.open(start_info.tenant_id, start_info.call_session_id)
                except RuntimeError as exc:
                    logger.error("voice_realtime_open_failed", error=str(exc))
                    await call_service.end_call(
                        start_info.tenant_id, start_info.call_session_id,
                        outcome=CallOutcome.PROVIDER_FAILURE, failure_reason=str(exc),
                    )
                    final_outcome = CallOutcome.PROVIDER_FAILURE
                    break
                bridge_opened = True
                drain_task = asyncio.create_task(_drain_bridge_events())
                continue

            if event == "media":
                if not bridge_opened:
                    continue
                await bridge.send_caller_audio(payload.payload)
                continue

            if event == "stop":
                break

        if start_info is not None and final_outcome is None:
            await call_service.end_call(start_info.tenant_id, start_info.call_session_id, outcome=CallOutcome.UNRESOLVED)

    except WebSocketDisconnect:
        if start_info is not None and final_outcome is None:
            logger.info(
                "voice_realtime_disconnected", tenant_id=str(start_info.tenant_id),
                call_session_id=str(start_info.call_session_id),
            )
            await call_service.end_call(
                start_info.tenant_id, start_info.call_session_id,
                outcome=CallOutcome.UNRESOLVED, failure_reason="caller_disconnected",
            )
    finally:
        if bridge_opened:
            await bridge.close()
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
