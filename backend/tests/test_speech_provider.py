"""app/services/speech_provider.py — STT/TTS provider selection, honest
NOT_CONFIGURED defaults, and the deterministic test doubles."""

import pytest

from app.core.config import get_settings
from app.services.speech_provider import (
    DeepgramSTTProvider,
    DeterministicSTTProvider,
    DeterministicTTSProvider,
    ElevenLabsTTSProvider,
    NotConfiguredSTTProvider,
    NotConfiguredTTSProvider,
    get_stt_provider,
    get_tts_provider,
)

pytestmark = pytest.mark.asyncio


async def test_deterministic_stt_round_trips_text_as_bytes() -> None:
    provider = DeterministicSTTProvider()
    result, outcome = await provider.transcribe(b"hello world", mime_type="audio/x-mulaw")
    assert outcome.success is True
    assert result.text == "hello world"


async def test_deterministic_tts_round_trips_text_as_bytes() -> None:
    provider = DeterministicTTSProvider()
    audio, outcome = await provider.synthesize("hello there")
    assert outcome.success is True
    assert audio == b"hello there"


async def test_not_configured_stt_is_honest() -> None:
    provider = NotConfiguredSTTProvider()
    assert provider.is_connected is False
    result, outcome = await provider.transcribe(b"abc", mime_type="audio/x-mulaw")
    assert result is None
    assert outcome.success is False
    assert outcome.error_detail == "no speech-to-text provider configured"


async def test_not_configured_tts_is_honest() -> None:
    provider = NotConfiguredTTSProvider()
    assert provider.is_connected is False
    audio, outcome = await provider.synthesize("hello")
    assert audio is None
    assert outcome.success is False
    assert outcome.error_detail == "no text-to-speech provider configured"


async def test_get_stt_provider_defaults_to_not_configured_without_key(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STT_PROVIDER", "auto")
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", None)
    assert isinstance(get_stt_provider(), NotConfiguredSTTProvider)


async def test_get_stt_provider_uses_deepgram_when_key_present(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STT_PROVIDER", "auto")
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "fake-key")
    assert isinstance(get_stt_provider(), DeepgramSTTProvider)


async def test_get_tts_provider_defaults_to_not_configured_without_key(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "TTS_PROVIDER", "auto")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", None)
    assert isinstance(get_tts_provider(), NotConfiguredTTSProvider)


async def test_get_tts_provider_uses_elevenlabs_when_key_present(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "TTS_PROVIDER", "auto")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "fake-key")
    assert isinstance(get_tts_provider(), ElevenLabsTTSProvider)


async def test_get_stt_provider_deterministic_is_explicit_opt_in(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STT_PROVIDER", "deterministic")
    assert isinstance(get_stt_provider(), DeterministicSTTProvider)


async def test_providers_never_leak_api_key_via_repr() -> None:
    assert "sk-secret" not in repr(DeepgramSTTProvider("sk-secret"))
    assert "sk-secret" not in repr(ElevenLabsTTSProvider("sk-secret"))
