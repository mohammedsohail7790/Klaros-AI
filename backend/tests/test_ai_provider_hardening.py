"""Phase 12E: AI provider hardening — configurable timeout/retries, real
retry-with-backoff, error classification, and token-usage extraction. None
of these require a real API key — `_call_api` is monkeypatched throughout."""

import httpx
import pytest

from app.core.config import get_settings
from app.services.ai_provider import (
    AIErrorType,
    AnthropicAIProvider,
    OpenAIAIProvider,
    _ProviderResponse,
)

pytestmark = pytest.mark.asyncio


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


async def test_401_is_classified_as_authentication_and_never_retried() -> None:
    provider = OpenAIAIProvider(api_key="bad-key")
    calls = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        nonlocal calls
        calls += 1
        raise _http_status_error(401)

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is False
    assert outcome.error_type == AIErrorType.AUTHENTICATION
    assert calls == 1  # never retried — retrying an invalid key wastes time and quota


async def test_429_rate_limit_is_retried_with_backoff() -> None:
    provider = OpenAIAIProvider(api_key="test-key")
    provider._max_retries = 2
    calls = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        nonlocal calls
        calls += 1
        raise _http_status_error(429)

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is False
    assert outcome.error_type == AIErrorType.RATE_LIMIT
    assert calls == 3  # 1 initial + 2 retries, bounded by _max_retries
    assert outcome.retry_count == 2


async def test_5xx_is_retried_and_eventually_succeeds() -> None:
    provider = AnthropicAIProvider(api_key="test-key")
    provider._max_retries = 3
    calls = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_status_error(503)
        return _ProviderResponse(text="ok", input_tokens=10, output_tokens=5)

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is True
    assert calls == 3
    assert outcome.retry_count == 2  # succeeded on the 3rd attempt (2 retries happened)


async def test_400_bad_request_is_not_retried() -> None:
    provider = OpenAIAIProvider(api_key="test-key")
    calls = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        nonlocal calls
        calls += 1
        raise _http_status_error(400)

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is False
    assert calls == 1


async def test_network_error_is_retried() -> None:
    provider = OpenAIAIProvider(api_key="test-key")
    provider._max_retries = 1
    calls = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is False
    assert outcome.error_type == AIErrorType.NETWORK_ERROR
    assert calls == 2


async def test_timeout_is_classified_correctly() -> None:
    provider = OpenAIAIProvider(api_key="test-key")
    provider._max_retries = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        raise httpx.TimeoutException("timed out")

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is False
    assert outcome.error_type == AIErrorType.TIMEOUT


async def test_successful_call_extracts_token_usage() -> None:
    provider = OpenAIAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> _ProviderResponse:
        return _ProviderResponse(text="ok", input_tokens=123, output_tokens=45)

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is True
    assert outcome.input_tokens == 123
    assert outcome.output_tokens == 45
    assert outcome.provider == "openai"
    assert outcome.model


async def test_missing_usage_data_reports_none_not_fabricated_zero() -> None:
    """If the provider doesn't return usage info, report unavailable
    (None) rather than fabricating a 0 that looks like real data."""
    provider = OpenAIAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> _ProviderResponse:
        return _ProviderResponse(text="ok", input_tokens=None, output_tokens=None)

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is True
    assert outcome.input_tokens is None
    assert outcome.output_tokens is None


async def test_timeout_and_retries_are_configurable_via_settings(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "5.0")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "4")
    get_settings.cache_clear()
    try:
        provider = OpenAIAIProvider(api_key="test-key")
        assert provider._timeout_seconds == 5.0
        assert provider._max_retries == 4
    finally:
        monkeypatch.delenv("OPENAI_TIMEOUT_SECONDS")
        monkeypatch.delenv("OPENAI_MAX_RETRIES")
        get_settings.cache_clear()


async def test_deterministic_provider_generate_structured_is_honest_not_fabricated() -> None:
    from app.services.ai_provider import DeterministicAIProvider

    provider = DeterministicAIProvider()
    outcome = await provider.generate_structured("hello")
    assert outcome.success is False
    assert "no ai provider configured" in outcome.error_detail.lower()


async def test_key_is_redacted_from_error_detail_even_if_an_exception_message_embeds_it() -> None:
    """Defense in depth: httpx's own exception messages never include the
    key (verified separately), but this proves the boundary holds even in
    a worst-case scenario — a hypothetical future _call_api bug that
    accidentally puts the raw key into an exception message must still
    never let it reach error_detail."""
    provider = OpenAIAIProvider(api_key="sk-worst-case-leak-12345")
    provider._max_retries = 0

    async def fake_call(prompt: str) -> _ProviderResponse:
        raise RuntimeError("auth failed with key sk-worst-case-leak-12345")

    provider._call_api = fake_call  # type: ignore[method-assign]
    outcome = await provider.generate_structured("hello")

    assert outcome.success is False
    assert "sk-worst-case-leak-12345" not in outcome.error_detail
    assert "REDACTED" in outcome.error_detail
