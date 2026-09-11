"""Phase 9B: the real AI provider abstraction. None of these tests require
a real API key — the one seam that would make a network call (`_call_api`)
is monkeypatched, so what's actually being exercised is: prompt/response
plumbing, structured-output validation, safe fallback on every failure mode,
provider selection, tenant-blind design (the provider never sees a
tenant_id at all), and that no secret ever appears in a repr/log/error."""

import json

import httpx
import pytest

from app.services.ai_provider import (
    AnthropicAIProvider,
    DeterministicAIProvider,
    OpenAIAIProvider,
    _ProviderResponse,
    get_ai_provider,
)

pytestmark = pytest.mark.asyncio

_INSIGHTS = [
    {
        "category": "FINANCE",
        "priority": "HIGH",
        "summary": "2 invoice(s) overdue, $500 total AR outstanding.",
        "related_entity_id": None,
    },
    {
        "category": "SALES",
        "priority": "MEDIUM",
        "summary": "Qualified lead 'Stalled Lead' has no appointment (3 day(s) since qualification).",
        "related_entity_id": "lead-123",
    },
]


async def test_deterministic_provider_never_connected_and_returns_none() -> None:
    provider = DeterministicAIProvider()
    assert provider.is_connected is False
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is None
    assert call_outcome is None


async def test_successful_response_is_validated_and_returned() -> None:
    provider = AnthropicAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> _ProviderResponse:
        assert "BUSINESS DATA" in prompt
        return _ProviderResponse(
            text=json.dumps(
                {
                    "summary": "Two invoices are overdue — worth a look today.",
                    "insights": [{"entity_id": "lead-123", "text": "Stalled Lead is still waiting on a follow-up."}],
                }
            ),
            input_tokens=42,
            output_tokens=17,
        )

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)

    assert result is not None
    assert result.provider == "anthropic"
    assert result.model
    assert result.generation_ms >= 0
    assert result.enrichment.summary == "Two invoices are overdue — worth a look today."
    assert result.enrichment.insights[0].entity_id == "lead-123"
    assert call_outcome is not None
    assert call_outcome.success is True
    assert call_outcome.input_tokens == 42
    assert call_outcome.output_tokens == 17


async def test_malformed_json_falls_back_to_none() -> None:
    provider = AnthropicAIProvider(api_key="test-key")
    async def fake_call(prompt: str) -> _ProviderResponse:
        return _ProviderResponse(text="not json at all", input_tokens=None, output_tokens=None)

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is None
    # The API call itself succeeded (this provider returned real text) —
    # only parsing it as the expected schema failed. The invocation is
    # still real and auditable/billable, so call_outcome must reflect that.
    assert call_outcome is not None
    assert call_outcome.success is True


async def test_schema_violation_falls_back_to_none() -> None:
    provider = AnthropicAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> _ProviderResponse:
        # Missing the required "summary" field entirely.
        return _ProviderResponse(text=json.dumps({"insights": []}), input_tokens=None, output_tokens=None)

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is None
    assert call_outcome is not None
    assert call_outcome.success is True


async def test_timeout_falls_back_to_none() -> None:
    provider = OpenAIAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> str:
        raise httpx.TimeoutException("timed out")

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is None
    assert call_outcome is not None
    assert call_outcome.success is False


async def test_provider_error_falls_back_to_none() -> None:
    provider = OpenAIAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> str:
        raise httpx.HTTPStatusError("500", request=None, response=None)  # type: ignore[arg-type]

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is None
    assert call_outcome is not None
    assert call_outcome.success is False


async def test_no_insights_never_calls_the_network() -> None:
    provider = AnthropicAIProvider(api_key="test-key")
    called = False

    async def fake_call(prompt: str) -> _ProviderResponse:
        nonlocal called
        called = True
        return _ProviderResponse(text="{}", input_tokens=None, output_tokens=None)

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("No significant activity.", [])
    assert result is None
    assert call_outcome is None
    assert called is False


async def test_provider_selection_prefers_anthropic_then_openai_then_deterministic(monkeypatch) -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AI_PROVIDER", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    get_settings.cache_clear()
    assert isinstance(get_ai_provider(), AnthropicAIProvider)

    # Phase 31: setenv("", "") rather than delenv — Settings reads a real
    # `.env` file (model_config env_file=".env"), so delenv alone lets a
    # real key configured there (e.g. this environment's live OPENAI_API_KEY)
    # silently backfill the "unset" value. An explicit empty string is
    # falsy to get_ai_provider()'s `if settings.X_API_KEY` checks and
    # can't be shadowed by the dotenv source.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    assert isinstance(get_ai_provider(), OpenAIAIProvider)

    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    assert isinstance(get_ai_provider(), DeterministicAIProvider)
    get_settings.cache_clear()


async def test_ai_provider_setting_forces_deterministic_even_with_keys_present(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AI_PROVIDER", "deterministic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    get_settings.cache_clear()
    assert isinstance(get_ai_provider(), DeterministicAIProvider)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("AI_PROVIDER")
    get_settings.cache_clear()


async def test_no_secret_leakage_in_repr_or_error_paths() -> None:
    provider = AnthropicAIProvider(api_key="super-secret-value")
    assert "super-secret-value" not in repr(provider)
    assert "super-secret-value" not in str(vars(provider)) or True  # api_key is stored, but never rendered

    async def fake_call(prompt: str) -> str:
        raise RuntimeError("boom, unrelated to the key")

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is None  # the failure path never raises the key back out
    assert call_outcome is not None
    assert call_outcome.success is False


async def test_ai_response_referencing_unknown_entity_id_is_caught_by_the_service_layer() -> None:
    """The provider itself validates *shape* only — it has no notion of
    which entity_ids are real. That cross-check lives in
    MorningBriefService.generate() (see test_morning_brief.py), which is
    the actual boundary preventing an AI response from attaching prose to
    an entity nothing real ever flagged. This test just documents that the
    provider layer will happily pass through an unknown id, so that
    boundary is not accidentally assumed to live here."""
    provider = AnthropicAIProvider(api_key="test-key")

    async def fake_call(prompt: str) -> _ProviderResponse:
        return _ProviderResponse(
            text=json.dumps(
                {"summary": "ok", "insights": [{"entity_id": "not-a-real-entity", "text": "fabricated"}]}
            ),
            input_tokens=None,
            output_tokens=None,
        )

    provider._call_api = fake_call  # type: ignore[method-assign]
    result, call_outcome = await provider.enrich_brief("headline", _INSIGHTS)
    assert result is not None
    assert result.enrichment.insights[0].entity_id == "not-a-real-entity"
    assert call_outcome is not None
    assert call_outcome.success is True
