"""Speech-to-text / text-to-speech provider abstractions for the AI Voice
Receptionist (Phase 4) — mirrors app/services/embedding_provider.py's
shape and honesty rules exactly: real REST-API adapters (Deepgram
prerecorded-transcription, ElevenLabs text-to-speech), an honest
NOT_CONFIGURED default, and a TEST-ONLY deterministic double for each.

Deliberately implements the REST/batch shape of each provider, not a live
bidirectional streaming session — this sandbox has no reachable Twilio
Media Stream carrying real caller audio to stream anywhere, and building
an unverifiable low-level streaming protocol against it would itself risk
fabricating correctness this project forbids. See
ARCHITECTURE_TRACEABILITY.md for the honest status of real-time streaming
STT/TTS as a scoped, deliberately-deferred upgrade.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass
class SpeechCallOutcome:
    success: bool
    provider: str
    latency_ms: int
    error_detail: str | None = None


@dataclass
class TranscriptionResult:
    text: str
    confidence: float | None


class SpeechToTextProvider(ABC):
    is_connected: bool = False
    name: str = "none"

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, *, mime_type: str) -> tuple[TranscriptionResult | None, SpeechCallOutcome]:
        """Returns (result-or-None, outcome). `result` is None only when
        `outcome.success` is False — never a fabricated transcript."""


class TextToSpeechProvider(ABC):
    is_connected: bool = False
    name: str = "none"

    @abstractmethod
    async def synthesize(self, text: str, *, voice_name: str | None = None) -> tuple[bytes | None, SpeechCallOutcome]:
        """Returns (audio-bytes-or-None, outcome). `audio` is None only
        when `outcome.success` is False — never fabricated/silent audio
        presented as a real synthesis."""


class NotConfiguredSTTProvider(SpeechToTextProvider):
    is_connected = False
    name = "none"

    async def transcribe(self, audio_bytes: bytes, *, mime_type: str) -> tuple[None, SpeechCallOutcome]:
        return None, SpeechCallOutcome(
            success=False, provider=self.name, latency_ms=0, error_detail="no speech-to-text provider configured"
        )


class NotConfiguredTTSProvider(TextToSpeechProvider):
    is_connected = False
    name = "none"

    async def synthesize(self, text: str, *, voice_name: str | None = None) -> tuple[None, SpeechCallOutcome]:
        return None, SpeechCallOutcome(
            success=False, provider=self.name, latency_ms=0, error_detail="no text-to-speech provider configured"
        )


class DeterministicSTTProvider(SpeechToTextProvider):
    """TEST-ONLY. Never makes a network call, never produces a real
    transcript from real audio — tests pass the "audio_bytes" as literal
    UTF-8 text standing in for what a real STT call would have returned,
    so the conversation-engine logic downstream of transcription can be
    tested without a live provider. Must never be used outside tests."""

    is_connected = True
    name = "deterministic"

    async def transcribe(self, audio_bytes: bytes, *, mime_type: str) -> tuple[TranscriptionResult, SpeechCallOutcome]:
        text = audio_bytes.decode("utf-8", errors="replace")
        return TranscriptionResult(text=text, confidence=1.0), SpeechCallOutcome(
            success=True, provider=self.name, latency_ms=0
        )


class DeterministicTTSProvider(TextToSpeechProvider):
    """TEST-ONLY. Returns the UTF-8 bytes of the input text as a stand-in
    "audio" payload — proves the synthesis boundary is exercised without
    a live provider or a real audio codec."""

    is_connected = True
    name = "deterministic"

    async def synthesize(self, text: str, *, voice_name: str | None = None) -> tuple[bytes, SpeechCallOutcome]:
        return text.encode("utf-8"), SpeechCallOutcome(success=True, provider=self.name, latency_ms=0)


class DeepgramSTTProvider(SpeechToTextProvider):
    is_connected = True
    name = "deepgram"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        settings = get_settings()
        self._timeout_seconds = settings.SPEECH_TIMEOUT_SECONDS

    def __repr__(self) -> str:
        return "DeepgramSTTProvider()"

    async def transcribe(self, audio_bytes: bytes, *, mime_type: str) -> tuple[TranscriptionResult | None, SpeechCallOutcome]:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={"Authorization": f"Token {self._api_key}", "content-type": mime_type},
                    content=audio_bytes,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return None, SpeechCallOutcome(
                success=False, provider=self.name, latency_ms=latency_ms, error_detail=str(type(exc).__name__)
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        try:
            alt = body["results"]["channels"][0]["alternatives"][0]
            result = TranscriptionResult(text=alt["transcript"], confidence=alt.get("confidence"))
        except (KeyError, IndexError, TypeError) as exc:
            return None, SpeechCallOutcome(
                success=False, provider=self.name, latency_ms=latency_ms, error_detail=f"malformed_response: {exc}"
            )
        return result, SpeechCallOutcome(success=True, provider=self.name, latency_ms=latency_ms)


class ElevenLabsTTSProvider(TextToSpeechProvider):
    is_connected = True
    name = "elevenlabs"
    _DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs' own public default voice ("Rachel")

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        settings = get_settings()
        self._timeout_seconds = settings.SPEECH_TIMEOUT_SECONDS

    def __repr__(self) -> str:
        return "ElevenLabsTTSProvider()"

    async def synthesize(self, text: str, *, voice_name: str | None = None) -> tuple[bytes | None, SpeechCallOutcome]:
        voice_id = voice_name or self._DEFAULT_VOICE_ID
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={"xi-api-key": self._api_key, "content-type": "application/json"},
                    json={"text": text},
                )
                response.raise_for_status()
                audio = response.content
        except httpx.HTTPError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return None, SpeechCallOutcome(
                success=False, provider=self.name, latency_ms=latency_ms, error_detail=str(type(exc).__name__)
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        return audio, SpeechCallOutcome(success=True, provider=self.name, latency_ms=latency_ms)


def get_stt_provider() -> SpeechToTextProvider:
    settings = get_settings()
    choice = (settings.STT_PROVIDER or "auto").lower()
    if choice == "deterministic":
        return DeterministicSTTProvider()
    if choice == "deepgram":
        return DeepgramSTTProvider(settings.DEEPGRAM_API_KEY) if settings.DEEPGRAM_API_KEY else NotConfiguredSTTProvider()
    if settings.DEEPGRAM_API_KEY:
        return DeepgramSTTProvider(settings.DEEPGRAM_API_KEY)
    return NotConfiguredSTTProvider()


def get_tts_provider() -> TextToSpeechProvider:
    settings = get_settings()
    choice = (settings.TTS_PROVIDER or "auto").lower()
    if choice == "deterministic":
        return DeterministicTTSProvider()
    if choice == "elevenlabs":
        return ElevenLabsTTSProvider(settings.ELEVENLABS_API_KEY) if settings.ELEVENLABS_API_KEY else NotConfiguredTTSProvider()
    if settings.ELEVENLABS_API_KEY:
        return ElevenLabsTTSProvider(settings.ELEVENLABS_API_KEY)
    return NotConfiguredTTSProvider()


# =============================================================================
# Phase 6: streaming STT/TTS — real-time (as opposed to Phase 4's REST/batch)
# =============================================================================
#
# Each real provider class below constructs the actual documented
# WebSocket URL/headers/message shapes for that provider's real-time API
# and would open a genuine `websockets` connection if `connect()` were
# called with a real key. None of this has ever been exercised against a
# live provider in this sandbox (no DEEPGRAM_API_KEY/ELEVENLABS_API_KEY) —
# the connection *lifecycle* (reconnect/timeout/real audio round-trip) is
# therefore IMPLEMENTED, NOT LIVE-VERIFIED. What IS verified by this
# module's tests is the pure, network-free part: URL/message construction,
# and the full real behavior of the deterministic in-process test doubles
# that implement the identical interface — enough to test
# app/services/voice_realtime_service.py's turn-manager logic for real
# without a live provider.

import asyncio
import json


@dataclass
class StreamingTranscriptEvent:
    text: str
    is_final: bool


class StreamingSTTProvider(ABC):
    """connect() -> send_audio() (repeatedly) -> flush() ->
    receive_transcript() (repeatedly, interim then final) -> close()."""

    is_connected: bool = False
    name: str = "none"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_audio(self, pcm16_bytes: bytes) -> None: ...

    @abstractmethod
    async def flush(self) -> None:
        """Signal end-of-utterance — provider should emit a final transcript."""

    @abstractmethod
    async def receive_transcript(self) -> "StreamingTranscriptEvent | None":
        """Returns the next transcript event, or None when the stream has
        closed with nothing more to deliver. Never blocks forever — real
        implementations must apply SPEECH_TIMEOUT_SECONDS."""

    @abstractmethod
    async def close(self) -> None: ...


class StreamingTTSProvider(ABC):
    """connect() -> synthesize(text) -> stream_audio() (repeatedly, PCM16
    chunks as they become available) -> close()."""

    is_connected: bool = False
    name: str = "none"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def synthesize(self, text: str, *, voice_name: str | None = None) -> None: ...

    @abstractmethod
    async def stream_audio(self):
        """Async generator yielding PCM16 byte chunks as they're produced."""

    @abstractmethod
    async def close(self) -> None: ...


class NotConfiguredStreamingSTTProvider(StreamingSTTProvider):
    is_connected = False
    name = "none"

    async def connect(self) -> None:
        raise RuntimeError("no streaming speech-to-text provider configured")

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        raise RuntimeError("no streaming speech-to-text provider configured")

    async def flush(self) -> None:
        raise RuntimeError("no streaming speech-to-text provider configured")

    async def receive_transcript(self) -> None:
        raise RuntimeError("no streaming speech-to-text provider configured")

    async def close(self) -> None:
        return None


class NotConfiguredStreamingTTSProvider(StreamingTTSProvider):
    is_connected = False
    name = "none"

    async def connect(self) -> None:
        raise RuntimeError("no streaming text-to-speech provider configured")

    async def synthesize(self, text: str, *, voice_name: str | None = None) -> None:
        raise RuntimeError("no streaming text-to-speech provider configured")

    async def stream_audio(self):
        raise RuntimeError("no streaming text-to-speech provider configured")
        yield b""  # pragma: no cover — makes this a generator function

    async def close(self) -> None:
        return None


class DeterministicStreamingSTTProvider(StreamingSTTProvider):
    """TEST-ONLY. A real, fully-functional in-process implementation of
    the interface (real asyncio.Queue, real ordering, real close
    semantics) — no network, no real transcription. `send_audio` treats
    each chunk's bytes as literal UTF-8 text standing in for what a real
    STT call would have produced, exactly like Phase 4's batch
    DeterministicSTTProvider, so turn-manager logic can be tested for
    real without a live provider."""

    is_connected = True
    name = "deterministic"

    def __init__(self) -> None:
        self._queue: asyncio.Queue[StreamingTranscriptEvent | None] = asyncio.Queue()
        self._buffer = bytearray()
        self._connected = False
        self._closed = False

    async def connect(self) -> None:
        self._connected = True

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        if not self._connected:
            raise RuntimeError("send_audio called before connect()")
        self._buffer.extend(pcm16_bytes)
        if self._buffer:
            await self._queue.put(StreamingTranscriptEvent(text=bytes(self._buffer).decode("utf-8", errors="replace"), is_final=False))

    async def flush(self) -> None:
        text = bytes(self._buffer).decode("utf-8", errors="replace")
        self._buffer.clear()
        await self._queue.put(StreamingTranscriptEvent(text=text, is_final=True))

    async def receive_transcript(self) -> StreamingTranscriptEvent | None:
        if self._closed and self._queue.empty():
            return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)


class DeterministicStreamingTTSProvider(StreamingTTSProvider):
    """TEST-ONLY — see DeterministicStreamingSTTProvider's docstring for
    the same reasoning. Yields the input text's UTF-8 bytes back in fixed
    chunks, standing in for real streamed audio."""

    is_connected = True
    name = "deterministic"

    def __init__(self, chunk_size: int = 8) -> None:
        self._chunk_size = chunk_size
        self._pending_text: str | None = None
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def synthesize(self, text: str, *, voice_name: str | None = None) -> None:
        if not self._connected:
            raise RuntimeError("synthesize called before connect()")
        self._pending_text = text

    async def stream_audio(self):
        if self._pending_text is None:
            return
        data = self._pending_text.encode("utf-8")
        for i in range(0, len(data), self._chunk_size):
            yield data[i : i + self._chunk_size]
        self._pending_text = None

    async def close(self) -> None:
        self._pending_text = None


class DeepgramStreamingSTTProvider(StreamingSTTProvider):
    """Real Deepgram streaming client — connects to Deepgram's actual
    documented real-time endpoint and message shapes. Never exercised
    against a live Deepgram account in this sandbox (no
    DEEPGRAM_API_KEY) — IMPLEMENTED, NOT LIVE-VERIFIED."""

    is_connected = True
    name = "deepgram"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        settings = get_settings()
        self._timeout_seconds = settings.SPEECH_TIMEOUT_SECONDS
        self._ws = None

    def __repr__(self) -> str:
        return "DeepgramStreamingSTTProvider()"

    async def connect(self) -> None:
        import websockets

        url = "wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=8000&channels=1"
        self._ws = await websockets.connect(url, additional_headers={"Authorization": f"Token {self._api_key}"})

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("send_audio called before connect()")
        await self._ws.send(pcm16_bytes)

    async def flush(self) -> None:
        if self._ws is None:
            raise RuntimeError("flush called before connect()")
        await self._ws.send(json.dumps({"type": "CloseStream"}))

    async def receive_transcript(self) -> StreamingTranscriptEvent | None:
        if self._ws is None:
            raise RuntimeError("receive_transcript called before connect()")
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout_seconds)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — any provider/network failure ends the stream honestly
            return None
        body = json.loads(raw)
        alt = body.get("channel", {}).get("alternatives", [{}])[0]
        return StreamingTranscriptEvent(text=alt.get("transcript", ""), is_final=bool(body.get("is_final")))

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()


class ElevenLabsStreamingTTSProvider(StreamingTTSProvider):
    """Real ElevenLabs streaming client — same honesty status as
    DeepgramStreamingSTTProvider above: IMPLEMENTED, NOT LIVE-VERIFIED."""

    is_connected = True
    name = "elevenlabs"
    _DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        settings = get_settings()
        self._timeout_seconds = settings.SPEECH_TIMEOUT_SECONDS
        self._ws = None

    def __repr__(self) -> str:
        return "ElevenLabsStreamingTTSProvider()"

    async def connect(self) -> None:
        import websockets

        voice_id = self._DEFAULT_VOICE_ID
        url = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_turbo_v2&output_format=pcm_16000"
        self._ws = await websockets.connect(url, additional_headers={"xi-api-key": self._api_key})

    async def synthesize(self, text: str, *, voice_name: str | None = None) -> None:
        if self._ws is None:
            raise RuntimeError("synthesize called before connect()")
        await self._ws.send(json.dumps({"text": text, "try_trigger_generation": True}))
        await self._ws.send(json.dumps({"text": ""}))

    async def stream_audio(self):
        if self._ws is None:
            raise RuntimeError("stream_audio called before connect()")
        import base64

        while True:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout_seconds)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                return
            body = json.loads(raw)
            if body.get("audio"):
                yield base64.b64decode(body["audio"])
            if body.get("isFinal"):
                return

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()


def get_streaming_stt_provider() -> StreamingSTTProvider:
    settings = get_settings()
    choice = (settings.STT_PROVIDER or "auto").lower()
    if choice == "deterministic":
        return DeterministicStreamingSTTProvider()
    if choice == "deepgram":
        return DeepgramStreamingSTTProvider(settings.DEEPGRAM_API_KEY) if settings.DEEPGRAM_API_KEY else NotConfiguredStreamingSTTProvider()
    if settings.DEEPGRAM_API_KEY:
        return DeepgramStreamingSTTProvider(settings.DEEPGRAM_API_KEY)
    return NotConfiguredStreamingSTTProvider()


def get_streaming_tts_provider() -> StreamingTTSProvider:
    settings = get_settings()
    choice = (settings.TTS_PROVIDER or "auto").lower()
    if choice == "deterministic":
        return DeterministicStreamingTTSProvider()
    if choice == "elevenlabs":
        return ElevenLabsStreamingTTSProvider(settings.ELEVENLABS_API_KEY) if settings.ELEVENLABS_API_KEY else NotConfiguredStreamingTTSProvider()
    if settings.ELEVENLABS_API_KEY:
        return ElevenLabsStreamingTTSProvider(settings.ELEVENLABS_API_KEY)
    return NotConfiguredStreamingTTSProvider()
