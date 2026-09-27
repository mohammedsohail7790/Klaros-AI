"""Embedding provider abstraction for the knowledge-retrieval layer (Phase 3
RAG) — mirrors app/services/ai_provider.py's shape and honesty rules
exactly: `get_embedding_provider()` is the only place that decides which
provider is active, based on real configuration; nothing else constructs a
provider directly.

Anthropic has no native embeddings API (their own docs point users to
Voyage AI) — there is deliberately no `AnthropicEmbeddingProvider` here;
inventing one would be exactly the fabricated-integration this project
forbids. Only OpenAI (`text-embedding-3-small` by default) is implemented
as a real provider; `DeterministicEmbeddingProvider` is explicitly
TEST-ONLY (see its own docstring) and must never be presented as a
production embedding.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.knowledge import EMBEDDING_DIMENSIONS

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class EmbeddingCallOutcome:
    """Mirrors app/services/ai_provider.py's AICallOutcome shape so
    embedding calls can be logged through the same
    record_ai_invocation() path (see KnowledgeRetrievalService)."""

    success: bool
    provider: str
    model: str
    latency_ms: int
    input_count: int
    error_detail: str | None = None


class EmbeddingProvider(ABC):
    is_connected: bool = False
    name: str = "none"
    dimensions: int
    model_name: str

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> tuple[list[list[float]] | None, EmbeddingCallOutcome]:
        """Returns (vectors-or-None, outcome). `vectors` is None only when
        `outcome.success` is False — never a fabricated all-zero result
        presented as a real embedding."""

    async def embed(self, text: str) -> tuple[list[float] | None, EmbeddingCallOutcome]:
        vectors, outcome = await self.embed_batch([text])
        return (vectors[0] if vectors else None), outcome


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """TEST-ONLY. A deterministic, dependency-free "hashed bag-of-words"
    vector: no external call, no real semantic meaning — it exists purely
    so retrieval/ranking LOGIC (chunking, cosine similarity, top-k,
    threshold, tenant filtering) can be proven correct in tests without an
    API key. Two texts sharing more words get a higher cosine similarity
    than two that don't, which is enough to test ranking order, but the
    actual vectors carry no real semantic understanding whatsoever and
    must never be described as "AI embeddings" in any user-facing text."""

    is_connected = True
    name = "deterministic"
    model_name = "deterministic-bag-of-words-test-double"

    def __init__(self, dimensions: int = 64) -> None:
        # Defaults to 64 (fast, unchanged behavior for every existing
        # caller/test). Tests that specifically exercise the real
        # PostgreSQL pgvector column (fixed at 1536 — OpenAI's real
        # dimension, see app/models/knowledge.py::EMBEDDING_DIMENSIONS)
        # pass dimensions=1536 so the vectors are realistically shaped
        # without needing a real OpenAI API key — still explicitly
        # TEST-ONLY, never constructed this way by get_embedding_provider().
        self.dimensions = dimensions

    async def embed_batch(self, texts: list[str]) -> tuple[list[list[float]], EmbeddingCallOutcome]:
        start = time.monotonic()
        vectors = [self._embed_one(t) for t in texts]
        latency_ms = int((time.monotonic() - start) * 1000)
        return vectors, EmbeddingCallOutcome(
            success=True, provider=self.name, model=self.model_name, latency_ms=latency_ms, input_count=len(texts)
        )

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD_RE.findall(text.lower()):
            bucket = int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimensions
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class NotConfiguredEmbeddingProvider(EmbeddingProvider):
    """The real default when no embedding provider is configured — never
    silently falls back to the deterministic test double for actual
    tenant usage (that would be exactly the "represent deterministic
    vectors as production AI embeddings" this project forbids). Indexing/
    search callers must treat `is_connected=False` as "unavailable,
    honestly report NOT_CONFIGURED" — never proceed with a fake vector."""

    is_connected = False
    name = "none"
    dimensions = 0
    model_name = "none"

    async def embed_batch(self, texts: list[str]) -> tuple[None, EmbeddingCallOutcome]:
        return None, EmbeddingCallOutcome(
            success=False, provider=self.name, model=self.model_name, latency_ms=0,
            input_count=len(texts), error_detail="no embedding provider configured",
        )


class OpenAIEmbeddingProvider(EmbeddingProvider):
    is_connected = True
    name = "openai"
    dimensions = 1536  # text-embedding-3-small's native dimension

    def __init__(self, api_key: str, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key
        self.model_name = model or settings.EMBEDDING_MODEL
        self._timeout_seconds = settings.OPENAI_TIMEOUT_SECONDS

    def __repr__(self) -> str:  # never leak the key via a repr/log
        return f"OpenAIEmbeddingProvider(model={self.model_name!r})"

    async def embed_batch(self, texts: list[str]) -> tuple[list[list[float]] | None, EmbeddingCallOutcome]:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"},
                    json={"model": self.model_name, "input": texts},
                )
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return None, EmbeddingCallOutcome(
                success=False, provider=self.name, model=self.model_name, latency_ms=latency_ms,
                input_count=len(texts), error_detail=str(type(exc).__name__),
            )
        latency_ms = int((time.monotonic() - start) * 1000)
        try:
            ordered = sorted(body["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in ordered]
        except (KeyError, TypeError) as exc:
            return None, EmbeddingCallOutcome(
                success=False, provider=self.name, model=self.model_name, latency_ms=latency_ms,
                input_count=len(texts), error_detail=f"malformed_response: {exc}",
            )
        return vectors, EmbeddingCallOutcome(
            success=True, provider=self.name, model=self.model_name, latency_ms=latency_ms, input_count=len(texts)
        )


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    choice = (settings.EMBEDDING_PROVIDER or "auto").lower()

    if choice == "deterministic":
        # Explicit opt-in only — tests set EMBEDDING_PROVIDER=deterministic
        # (see tests/conftest.py) to exercise real retrieval/ranking logic
        # without an API key. Never the silent default for real usage.
        # Must match KnowledgeChunk.embedding's real fixed-width column
        # (app/models/knowledge.py::EMBEDDING_DIMENSIONS, vector(1536) on
        # PostgreSQL) — the class default of 64 exists only for tests that
        # construct DeterministicEmbeddingProvider() directly for pure
        # ranking-logic checks unrelated to the real DB column.
        return DeterministicEmbeddingProvider(dimensions=EMBEDDING_DIMENSIONS)
    if choice == "openai":
        return OpenAIEmbeddingProvider(settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else NotConfiguredEmbeddingProvider()

    # "auto" (default): real OpenAI embeddings if configured, else honestly
    # NOT_CONFIGURED — never silently degrades production retrieval to the
    # deterministic test double.
    if settings.OPENAI_API_KEY:
        return OpenAIEmbeddingProvider(settings.OPENAI_API_KEY)
    return NotConfiguredEmbeddingProvider()
