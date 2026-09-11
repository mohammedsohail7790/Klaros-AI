"""Phase 6: streaming STT/TTS provider selection and the deterministic
in-process test doubles (real asyncio queues/generators, no network) —
proves the interface itself works correctly, independent of any live
provider."""

import pytest

from app.core.config import get_settings
from app.services.speech_provider import (
    DeepgramStreamingSTTProvider,
    DeterministicStreamingSTTProvider,
    DeterministicStreamingTTSProvider,
    ElevenLabsStreamingTTSProvider,
    NotConfiguredStreamingSTTProvider,
    NotConfiguredStreamingTTSProvider,
    get_streaming_stt_provider,
    get_streaming_tts_provider,
)

pytestmark = pytest.mark.asyncio


async def test_streaming_stt_defaults_to_not_configured_without_key(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STT_PROVIDER", "auto")
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", None)
    assert isinstance(get_streaming_stt_provider(), NotConfiguredStreamingSTTProvider)


async def test_streaming_stt_uses_deepgram_when_key_present(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STT_PROVIDER", "auto")
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "fake-key")
    assert isinstance(get_streaming_stt_provider(), DeepgramStreamingSTTProvider)


async def test_streaming_tts_uses_elevenlabs_when_key_present(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "TTS_PROVIDER", "auto")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "fake-key")
    assert isinstance(get_streaming_tts_provider(), ElevenLabsStreamingTTSProvider)


async def test_not_configured_streaming_stt_fails_honestly() -> None:
    provider = NotConfiguredStreamingSTTProvider()
    with pytest.raises(RuntimeError):
        await provider.connect()


async def test_not_configured_streaming_tts_fails_honestly() -> None:
    provider = NotConfiguredStreamingTTSProvider()
    with pytest.raises(RuntimeError):
        await provider.connect()


async def test_deterministic_streaming_stt_delivers_interim_then_final() -> None:
    provider = DeterministicStreamingSTTProvider()
    await provider.connect()
    await provider.send_audio(b"hello ")
    await provider.send_audio(b"world")

    interim1 = await provider.receive_transcript()
    assert interim1.is_final is False
    assert interim1.text == "hello "

    interim2 = await provider.receive_transcript()
    assert interim2.is_final is False
    assert interim2.text == "hello world"

    await provider.flush()
    final = await provider.receive_transcript()
    assert final.is_final is True
    assert final.text == "hello world"

    await provider.close()
    end = await provider.receive_transcript()
    assert end is None


async def test_deterministic_streaming_stt_rejects_use_before_connect() -> None:
    provider = DeterministicStreamingSTTProvider()
    with pytest.raises(RuntimeError):
        await provider.send_audio(b"too early")


async def test_deterministic_streaming_tts_yields_chunks_in_order() -> None:
    provider = DeterministicStreamingTTSProvider(chunk_size=4)
    await provider.connect()
    await provider.synthesize("hello world")

    chunks = [c async for c in provider.stream_audio()]
    assert b"".join(chunks) == b"hello world"
    assert all(len(c) <= 4 for c in chunks)
    await provider.close()


async def test_deterministic_streaming_tts_rejects_use_before_connect() -> None:
    provider = DeterministicStreamingTTSProvider()
    with pytest.raises(RuntimeError):
        await provider.synthesize("too early")
