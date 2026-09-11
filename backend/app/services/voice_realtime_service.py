"""Phase 6: the real-time turn manager — sits between the Twilio Media
Streams WebSocket (app/api/v1/voice_stream.py) and the existing,
UNCHANGED Phase 4/5 VoiceConversationService. This module owns exactly the
real-time concerns that are genuinely new in Phase 6 (audio buffering,
silence detection, barge-in, streaming TTS playback, per-turn latency) and
deliberately owns NONE of the conversation/booking logic — every
transcript this module produces still goes through
`VoiceConversationService.handle_turn` unchanged, so the deterministic
Phase 5 booking state machine remains the sole source of truth for what
the caller is doing.

Design note on testability: real-time audio pipelines are normally driven
by wall-clock timers and concurrent tasks, which are hard to test
deterministically. Every policy decision here (silence handling, barge-in)
is instead a pure function or an explicitly-invoked method — the WebSocket
route drives the clock (one call per Twilio media frame, ~20ms of audio
each), so tests can drive the exact same methods without real time passing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import get_settings
from app.services.audio_codec import mulaw_to_pcm16_bytes, pcm16_bytes_to_mulaw, rms_energy
from app.services.speech_provider import StreamingSTTProvider, StreamingTTSProvider
from app.services.voice_call_service import VoiceCallService
from app.services.voice_conversation_service import VoiceConversationService


class SilenceAction(StrEnum):
    CONTINUE = "CONTINUE"
    UTTERANCE_END = "UTTERANCE_END"
    PROMPT = "PROMPT"
    HANGUP = "HANGUP"


def decide_silence_action(*, consecutive_silence_ticks: int, prompts_sent: int, has_pending_speech: bool = False) -> SilenceAction:
    """Pure, deterministic — never calls the LLM for silence handling
    (mission requirement). `prompts_sent` resets to 0 whenever the caller
    speaks again (tracked by the caller of this function). When the caller
    has said something since the last processed turn (`has_pending_speech`),
    a shorter silence gap means "done talking, go ahead and process it" —
    the longer prompt/hangup thresholds only apply to a caller who hasn't
    said anything at all yet."""
    settings = get_settings()
    if has_pending_speech and consecutive_silence_ticks >= settings.VOICE_UTTERANCE_END_SILENCE_TICKS:
        return SilenceAction.UTTERANCE_END
    if consecutive_silence_ticks < settings.VOICE_SILENCE_PROMPT_AFTER_TICKS:
        return SilenceAction.CONTINUE
    if prompts_sent < settings.VOICE_SILENCE_HANGUP_AFTER_PROMPTS:
        return SilenceAction.PROMPT
    return SilenceAction.HANGUP


@dataclass
class TurnLatency:
    stt_ms: int | None = None
    conversation_ms: int | None = None
    tts_ms: int | None = None
    total_ms: int | None = None


class BargeInController:
    """Tracks whether the agent is currently "speaking" (streaming TTS
    audio to the caller) so a real caller utterance arriving mid-playback
    can interrupt it. Owns no I/O itself — the WebSocket route is
    responsible for actually stopping outbound audio frames and sending
    Twilio's `clear` event; this class only owns the true/false state and
    the cooperative-cancellation flag real code checks between chunks."""

    def __init__(self) -> None:
        self._speaking = False
        self._interrupt_requested = False

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def start_speaking(self) -> None:
        self._speaking = True
        self._interrupt_requested = False

    def stop_speaking(self) -> None:
        self._speaking = False
        self._interrupt_requested = False

    def interrupt(self) -> bool:
        """Called when real caller speech arrives while the agent is
        speaking. Returns True if an interruption actually happened (i.e.
        the agent really was speaking) so the caller can decide whether to
        emit Twilio's `clear` event."""
        if not self._speaking:
            return False
        self._interrupt_requested = True
        self._speaking = False
        return True

    @property
    def interrupt_requested(self) -> bool:
        """Checked between outgoing TTS chunks — real streaming code
        should stop sending audio the moment this becomes True rather
        than after the full response has been sent."""
        return self._interrupt_requested


@dataclass
class RealtimeCallState:
    """Per-connection state for one active Media Stream — the parts that
    are genuinely real-time-only and don't belong in the persisted
    `CallSession` (which already holds transcript/booking/outcome, see
    Phase 4/5). Lost on disconnect by design; anything that must survive
    a reconnect is already in `CallSession`."""

    tenant_id: uuid.UUID
    call_id: uuid.UUID
    silence_ticks: int = 0
    prompts_sent: int = 0
    has_pending_speech: bool = False
    barge_in: BargeInController = field(default_factory=BargeInController)
    last_latency: TurnLatency | None = None


class RealtimeTurnManager:
    def __init__(
        self,
        call_service: VoiceCallService,
        conversation_service: VoiceConversationService,
        stt_provider: StreamingSTTProvider,
        tts_provider: StreamingTTSProvider,
    ) -> None:
        self._calls = call_service
        self._conversation = conversation_service
        self._stt = stt_provider
        self._tts = tts_provider

    def is_silent_frame(self, mulaw_payload: bytes) -> bool:
        settings = get_settings()
        pcm = mulaw_to_pcm16_bytes(mulaw_payload)
        return rms_energy(pcm) < settings.VOICE_SILENCE_RMS_THRESHOLD

    async def handle_media_frame(self, state: RealtimeCallState, mulaw_payload: bytes) -> SilenceAction | None:
        """Called once per inbound Twilio `media` event. Feeds real audio
        to STT (converted from mulaw to PCM16), tracks silence, and
        handles barge-in if the agent is currently speaking. Returns a
        SilenceAction only when one has just newly triggered (so the
        route doesn't re-prompt every single frame)."""
        silent = self.is_silent_frame(mulaw_payload)
        pcm = mulaw_to_pcm16_bytes(mulaw_payload)

        if not silent and state.barge_in.is_speaking:
            state.barge_in.interrupt()

        await self._stt.send_audio(pcm)

        if silent:
            state.silence_ticks += 1
            action = decide_silence_action(
                consecutive_silence_ticks=state.silence_ticks, prompts_sent=state.prompts_sent,
                has_pending_speech=state.has_pending_speech,
            )
            if action == SilenceAction.UTTERANCE_END:
                state.silence_ticks = 0
                state.has_pending_speech = False
                return action
            if action == SilenceAction.PROMPT:
                state.prompts_sent += 1
                state.silence_ticks = 0
                return action
            if action == SilenceAction.HANGUP:
                return action
            return None

        state.silence_ticks = 0
        state.prompts_sent = 0
        state.has_pending_speech = True
        return None

    async def run_conversation_turn(self, state: RealtimeCallState, transcript_text: str):
        """Runs the transcript through the existing, UNCHANGED
        VoiceConversationService.handle_turn — the Phase 5 deterministic
        booking state machine remains fully authoritative; this method
        adds nothing to that decision beyond timing it. Returns the real
        `TurnResult` (see voice_conversation_service.py). Call
        `stream_reply_audio()` afterward to actually speak `reply_text`."""
        conv_start = time.monotonic()
        result = await self._conversation.handle_turn(state.tenant_id, state.call_id, transcript_text)
        conversation_ms = int((time.monotonic() - conv_start) * 1000)
        state.last_latency = TurnLatency(conversation_ms=conversation_ms)
        return result

    async def stream_reply_audio(self, state: RealtimeCallState, reply_text: str):
        """Yields PCM16 audio chunks for `reply_text` as the (real,
        streaming) TTS provider produces them. Yields nothing at all if no
        TTS provider is configured — the caller still has the real text
        reply from `run_conversation_turn`, just no synthesized audio; this
        must never be treated as a failure by itself. Checks
        `state.barge_in.interrupt_requested` between chunks so a caller
        who starts talking mid-reply actually stops the agent."""
        if not self._tts.is_connected or not reply_text:
            await self._record_latency(state, state.last_latency or TurnLatency())
            return

        tts_start = time.monotonic()
        state.barge_in.start_speaking()
        try:
            await self._tts.connect()
            await self._tts.synthesize(reply_text)
            async for chunk in self._tts.stream_audio():
                if state.barge_in.interrupt_requested:
                    break
                yield chunk
        finally:
            state.barge_in.stop_speaking()
            await self._tts.close()

        latency = state.last_latency or TurnLatency()
        latency.tts_ms = int((time.monotonic() - tts_start) * 1000)
        latency.total_ms = (latency.conversation_ms or 0) + latency.tts_ms
        state.last_latency = latency
        await self._record_latency(state, latency)

    async def _record_latency(self, state: RealtimeCallState, latency: TurnLatency) -> None:
        call = await self._calls.get_call(state.tenant_id, state.call_id)
        history = list(call.engine_state.get("latency_ms") or [])
        history.append({
            "conversation_ms": latency.conversation_ms, "tts_ms": latency.tts_ms, "total_ms": latency.total_ms,
        })
        # Bounded — never grows unbounded across a very long call.
        history = history[-50:]
        await self._calls.update_call(state.tenant_id, state.call_id, engine_state={**call.engine_state, "latency_ms": history})

    def mulaw_chunk_from_pcm16(self, pcm16_bytes: bytes) -> bytes:
        return pcm16_bytes_to_mulaw(pcm16_bytes)
