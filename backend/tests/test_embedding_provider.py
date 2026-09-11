"""app/services/embedding_provider.py — the deterministic test double,
the honest NOT_CONFIGURED default, and get_embedding_provider() selection."""

import pytest

from app.core.config import get_settings
from app.services.embedding_provider import (
    DeterministicEmbeddingProvider,
    NotConfiguredEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)

pytestmark = pytest.mark.asyncio


async def test_deterministic_provider_is_connected_and_deterministic() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.is_connected is True
    v1, outcome1 = await provider.embed("the quick brown fox")
    v2, outcome2 = await provider.embed("the quick brown fox")
    assert v1 == v2
    assert outcome1.success is True
    assert outcome2.success is True
    assert len(v1) == provider.dimensions


async def test_deterministic_provider_differentiates_dissimilar_text() -> None:
    provider = DeterministicEmbeddingProvider()
    v_a, _ = await provider.embed("pricing rules for plumbing jobs")
    v_b, _ = await provider.embed("pricing rules for plumbing jobs")
    v_c, _ = await provider.embed("brand voice guide for marketing")
    assert v_a == v_b
    assert v_a != v_c


async def test_deterministic_embed_batch_matches_individual_embeds() -> None:
    provider = DeterministicEmbeddingProvider()
    texts = ["alpha beta", "gamma delta"]
    batch, outcome = await provider.embed_batch(texts)
    assert outcome.success is True
    individual = [(await provider.embed(t))[0] for t in texts]
    assert batch == individual


async def test_not_configured_provider_is_honest() -> None:
    provider = NotConfiguredEmbeddingProvider()
    assert provider.is_connected is False
    vectors, outcome = await provider.embed_batch(["anything"])
    assert vectors is None
    assert outcome.success is False
    assert "no embedding provider configured" == outcome.error_detail


async def test_get_embedding_provider_defaults_to_not_configured_without_a_key(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "auto")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    provider = get_embedding_provider()
    assert isinstance(provider, NotConfiguredEmbeddingProvider)


async def test_get_embedding_provider_uses_openai_when_key_present(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "auto")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-fake-test-key")
    provider = get_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)


async def test_get_embedding_provider_deterministic_is_explicit_opt_in(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "deterministic")
    provider = get_embedding_provider()
    assert isinstance(provider, DeterministicEmbeddingProvider)


async def test_openai_provider_never_leaks_the_api_key_via_repr() -> None:
    provider = OpenAIEmbeddingProvider("sk-super-secret-value")
    assert "sk-super-secret-value" not in repr(provider)
