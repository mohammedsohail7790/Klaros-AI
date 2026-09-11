"""Phase 6: the real-time turn manager. Silence/barge-in policy is tested
as pure/deterministic logic (no real timers); the conversation/TTS
integration is tested against the real VoiceConversationService (reusing
Phase 4/5's tool_registry-backed setup) and the deterministic streaming
speech providers — proving the Phase 5 booking state machine remains
authoritative and untouched by this new real-time layer."""

import json
import struct
import uuid

import pytest

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.services.ai_provider import AICallOutcome
from app.services.audio_codec import pcm16_to_mulaw_byte
from app.services.knowledge_qa_service import KnowledgeQAService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_service import KnowledgeService
from app.services.speech_provider import DeterministicStreamingSTTProvider, DeterministicStreamingTTSProvider
from app.services.voice_call_service import VoiceCallService
from app.services.voice_conversation_service import VoiceConversationService
from app.services.voice_realtime_service import (
    BargeInController,
    RealtimeCallState,
    RealtimeTurnManager,
    SilenceAction,
    decide_silence_action,
)

pytestmark = pytest.mark.asyncio


def _silent_mulaw_frame(n: int = 160) -> bytes:
    # 0xFF is G.711 mu-law's near-zero/silence byte.
    return bytes([0xFF] * n)


def _loud_mulaw_frame(n: int = 160) -> bytes:
    samples = [8000 if i % 2 == 0 else -8000 for i in range(n)]
    return bytes(pcm16_to_mulaw_byte(s) for s in samples)


# --- Silence policy: pure, deterministic ---

async def test_silence_action_continues_below_threshold() -> None:
    assert decide_silence_action(consecutive_silence_ticks=1, prompts_sent=0) == SilenceAction.CONTINUE


async def test_silence_action_prompts_after_threshold() -> None:
    settings = get_settings()
    assert decide_silence_action(
        consecutive_silence_ticks=settings.VOICE_SILENCE_PROMPT_AFTER_TICKS, prompts_sent=0
    ) == SilenceAction.PROMPT


async def test_silence_action_hangs_up_after_repeated_prompts() -> None:
    settings = get_settings()
    assert decide_silence_action(
        consecutive_silence_ticks=settings.VOICE_SILENCE_PROMPT_AFTER_TICKS,
        prompts_sent=settings.VOICE_SILENCE_HANGUP_AFTER_PROMPTS,
    ) == SilenceAction.HANGUP


# --- Barge-in controller: real object, no I/O ---

async def test_barge_in_interrupt_only_fires_while_speaking() -> None:
    controller = BargeInController()
    assert controller.interrupt() is False  # not speaking yet
    controller.start_speaking()
    assert controller.is_speaking is True
    assert controller.interrupt() is True
    assert controller.is_speaking is False
    assert controller.interrupt_requested is True


async def test_barge_in_stop_speaking_resets_interrupt_flag() -> None:
    controller = BargeInController()
    controller.start_speaking()
    controller.interrupt()
    controller.stop_speaking()
    assert controller.interrupt_requested is False


# --- Frame-level silence detection (real audio codec + energy) ---

async def _build_manager(tool_registry, ai_provider) -> tuple[RealtimeTurnManager, VoiceCallService]:
    call_service = VoiceCallService(async_session_maker)
    ai_execution = AIExecutionService(tool_registry)
    knowledge_service = KnowledgeService(async_session_maker)
    retrieval_service = KnowledgeRetrievalService(async_session_maker, knowledge_service)
    qa_service = KnowledgeQAService(async_session_maker, retrieval_service, ai_provider)
    conversation = VoiceConversationService(async_session_maker, call_service, ai_execution, ai_provider, qa_service)
    stt = DeterministicStreamingSTTProvider()
    tts = DeterministicStreamingTTSProvider(chunk_size=6)
    manager = RealtimeTurnManager(call_service, conversation, stt, tts)
    return manager, call_service


async def test_handle_media_frame_detects_silence_vs_speech(tool_registry) -> None:
    manager, call_service = await _build_manager(tool_registry, _make_never_called_provider())
    tenant_id = uuid.uuid4()
    call, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-rt-1", caller_number="+15550000001")
    await manager._stt.connect()
    state = RealtimeCallState(tenant_id=tenant_id, call_id=call.id)

    for _ in range(3):
        result = await manager.handle_media_frame(state, _silent_mulaw_frame())
        assert result is None
    assert state.silence_ticks == 3

    await manager.handle_media_frame(state, _loud_mulaw_frame())
    assert state.silence_ticks == 0


async def test_repeated_silence_triggers_prompt_then_hangup(tool_registry) -> None:
    manager, call_service = await _build_manager(tool_registry, _make_never_called_provider())
    tenant_id = uuid.uuid4()
    call, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-rt-2", caller_number="+15550000002")
    await manager._stt.connect()
    state = RealtimeCallState(tenant_id=tenant_id, call_id=call.id)

    settings = get_settings()
    actions = []
    for _ in range(settings.VOICE_SILENCE_PROMPT_AFTER_TICKS * (settings.VOICE_SILENCE_HANGUP_AFTER_PROMPTS + 1) + 5):
        action = await manager.handle_media_frame(state, _silent_mulaw_frame())
        if action is not None:
            actions.append(action)

    assert SilenceAction.PROMPT in actions
    assert actions[-1] == SilenceAction.HANGUP


def _make_never_called_provider():
    class _NeverCalled:
        is_connected = False
        name = "none"
        model = "none"

        async def generate_structured(self, prompt: str) -> AICallOutcome:
            raise AssertionError("should never be called in this test")

    return _NeverCalled()


# --- Barge-in during real audio streaming ---

async def test_caller_speech_during_agent_playback_stops_further_audio(tool_registry) -> None:
    class _FakeReplyingProvider:
        is_connected = True
        name = "fake"
        model = "fake-model-1"

        async def generate_structured(self, prompt: str) -> AICallOutcome:
            return AICallOutcome(
                success=True, provider=self.name, model=self.model, latency_ms=5,
                raw_text=json.dumps({
                    "intent": "SMALL_TALK", "wants_human": False, "name": None, "service_requested": None,
                    "location": None, "urgency": None,
                    "reply_text": "This is a somewhat long response that will be streamed in several chunks to the caller.",
                }),
            )

    manager, call_service = await _build_manager(tool_registry, _FakeReplyingProvider())
    tenant_id = uuid.uuid4()
    call, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-rt-3", caller_number="+15550000003")
    state = RealtimeCallState(tenant_id=tenant_id, call_id=call.id)

    result = await manager.run_conversation_turn(state, "just saying hello")
    assert result.reply_text

    chunks = []
    async for chunk in manager.stream_reply_audio(state, result.reply_text):
        chunks.append(chunk)
        if len(chunks) == 2:
            # Simulate the caller starting to talk mid-playback.
            state.barge_in.interrupt()

    # Playback stopped early — fewer chunks than the full reply would need.
    full_chunk_count = -(-len(result.reply_text.encode()) // 6)
    assert len(chunks) < full_chunk_count
    assert state.barge_in.is_speaking is False


# --- Full real-time turn: transcript in, real booking-flow reply out ---

async def test_realtime_turn_reaches_the_same_booking_flow_as_phase5(tool_registry) -> None:
    class _AppointmentProvider:
        is_connected = True
        name = "fake"
        model = "fake-model-1"

        async def generate_structured(self, prompt: str) -> AICallOutcome:
            return AICallOutcome(
                success=True, provider=self.name, model=self.model, latency_ms=5,
                raw_text=json.dumps({
                    "intent": "APPOINTMENT_REQUEST", "wants_human": False, "name": "Riley",
                    "service_requested": "AC repair", "location": None, "urgency": None,
                    "reply_text": "Sure, let me check availability.",
                }),
            )

    manager, call_service = await _build_manager(tool_registry, _AppointmentProvider())
    tenant_id = uuid.uuid4()
    call, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-rt-4", caller_number="+15550000004")
    state = RealtimeCallState(tenant_id=tenant_id, call_id=call.id)

    result = await manager.run_conversation_turn(state, "I need an AC repair appointment")
    assert "available" in result.reply_text.lower()

    async for _chunk in manager.stream_reply_audio(state, result.reply_text):
        pass

    updated = await call_service.get_call(tenant_id, call.id)
    assert updated.engine_state["booking"]["state"] == "OFFERING_SLOTS"
    assert state.last_latency.conversation_ms is not None
    assert state.last_latency.tts_ms is not None

    # Latency was persisted onto the real CallSession.
    assert updated.engine_state.get("latency_ms")


async def test_speech_then_silence_triggers_utterance_end_not_a_prompt(tool_registry) -> None:
    manager, call_service = await _build_manager(tool_registry, _make_never_called_provider())
    tenant_id = uuid.uuid4()
    call, _ = await call_service.get_or_create_call(tenant_id, provider="twilio", external_call_id="CA-rt-5", caller_number="+15550000005")
    await manager._stt.connect()
    state = RealtimeCallState(tenant_id=tenant_id, call_id=call.id)

    await manager.handle_media_frame(state, _loud_mulaw_frame())
    assert state.has_pending_speech is True

    settings = get_settings()
    action = None
    for _ in range(settings.VOICE_UTTERANCE_END_SILENCE_TICKS):
        action = await manager.handle_media_frame(state, _silent_mulaw_frame())
    assert action == SilenceAction.UTTERANCE_END
    assert state.has_pending_speech is False
    assert state.prompts_sent == 0  # never escalated to the "are you still there" path


async def test_mulaw_pcm_round_trip_through_the_manager() -> None:
    manager = RealtimeTurnManager.__new__(RealtimeTurnManager)  # pure helper method, no services needed
    pcm = struct.pack("<4h", 1000, -1000, 2000, -2000)
    mulaw = manager.mulaw_chunk_from_pcm16(pcm)
    assert len(mulaw) == 4
