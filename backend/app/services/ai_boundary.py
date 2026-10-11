"""The boundary between a consent-gated tenant's data and EXTERNAL AI / embedding providers.

Every service that sends tenant content to a model wraps its provider with `bound_ai_provider(...)` / `bound_embedding_provider(...)` at the point
where the tenant is known (tests/test_mt_hardening_ai_boundary.py statically proves no call site skips this). The wrapper decides per call:

  * a provider verified, by class, to make no external call (DeterministicAIProvider / DeterministicEmbeddingProvider) always passes;
  * a tenant that is not consent-gated is unchanged;
  * a consent-gated tenant (or one whose gating cannot be established: fail closed, or an unknown tenant) may use an EXTERNAL provider only if its
    name is in Settings.AI_EXTERNAL_PROCESSING_ALLOWED_PROVIDERS (default empty = none). Anything else is refused: the call returns the same failed
    outcome a missing provider does, so callers fall back to their deterministic path, and nothing leaves the process.
  * when an allowlisted external provider is used for a gated tenant, e-mail addresses and phone numbers are scrubbed from the outgoing text. FREE-TEXT
    MEDICAL DETAIL CANNOT BE RELIABLY REDACTED, which is why the allowlist stays empty until the owner has a lawful basis (AI-processing consent / a
    processor agreement): see docs/MEDICAL_TOURISM_POLICY_DECISIONS.md.
The deterministic provider is never ASSUMED: `effective_provider_report()` shows which class runtime configuration actually resolved to.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.core.config import get_settings
from app.services.ai_provider import AICallOutcome, AIErrorType, AIProvider, DeterministicAIProvider
from app.services.embedding_provider import DeterministicEmbeddingProvider, EmbeddingCallOutcome, EmbeddingProvider
from app.tools.redact import scrub_text

logger = structlog.get_logger()
BLOCKED_PREFIX = "blocked_by_ai_boundary"


def allowed_external_providers() -> frozenset[str]:
    raw = get_settings().AI_EXTERNAL_PROCESSING_ALLOWED_PROVIDERS or ""
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def _is_local(provider: Any) -> bool:
    return type(provider) in (DeterministicAIProvider, DeterministicEmbeddingProvider)


async def _decide(provider: Any, session_factory, tenant_id: uuid.UUID | None, purpose: str) -> tuple[bool, bool, str]:
    """(allowed, minimize, reason)."""
    if _is_local(provider):
        return True, False, "local_provider"
    from app.services import consent_gate

    if tenant_id is None:
        return False, False, "tenant_unknown"
    if not await consent_gate.tenant_requires_consent(session_factory, tenant_id):
        return True, False, "not_gated"
    if str(getattr(provider, "name", "")).lower() in allowed_external_providers():
        return True, True, "allowlisted"
    logger.warning("ai_boundary_blocked_external_call", tenant_id=str(tenant_id), provider=str(getattr(provider, "name", "?")), purpose=purpose)
    return False, False, "provider_not_allowlisted"


class BoundedAIProvider(AIProvider):
    def __init__(self, inner: AIProvider, session_factory, tenant_id: uuid.UUID | None, purpose: str) -> None:
        self._inner, self._sf, self._tenant, self._purpose = inner, session_factory, tenant_id, purpose
        self.name, self.model, self.is_connected = getattr(inner, "name", "unknown"), getattr(inner, "model", "unknown"), getattr(inner, "is_connected", False)

    def __getattr__(self, item: str):  # anything else (e.g. test doubles' attributes) passes through
        return getattr(self._inner, item)

    def _refused(self, reason: str) -> AICallOutcome:
        return AICallOutcome(
            success=False, provider=self.name, model=self.model, latency_ms=0, error_type=AIErrorType.PROVIDER_ERROR,
            error_detail=f"{BLOCKED_PREFIX}:{reason}",
        )

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        ok, minimize, reason = await _decide(self._inner, self._sf, self._tenant, self._purpose)
        if not ok:
            return self._refused(reason)
        return await self._inner.generate_structured(scrub_text(prompt) if minimize else prompt)

    async def enrich_brief(self, headline, insights, *, brand_voice=None, company_memory=None):
        ok, minimize, reason = await _decide(self._inner, self._sf, self._tenant, self._purpose)
        if not ok:
            return None, self._refused(reason)
        if minimize:
            headline = scrub_text(headline)
            insights = [{k: (scrub_text(v) if isinstance(v, str) else v) for k, v in i.items()} for i in insights]
            brand_voice = scrub_text(brand_voice) if brand_voice else brand_voice
            company_memory = scrub_text(company_memory) if company_memory else company_memory
        return await self._inner.enrich_brief(headline, insights, brand_voice=brand_voice, company_memory=company_memory)


class BoundedEmbeddingProvider(EmbeddingProvider):
    def __init__(self, inner: EmbeddingProvider, session_factory, tenant_id: uuid.UUID | None, purpose: str) -> None:
        self._inner, self._sf, self._tenant, self._purpose = inner, session_factory, tenant_id, purpose
        self.name, self.is_connected = getattr(inner, "name", "unknown"), getattr(inner, "is_connected", False)
        self.dimensions, self.model_name = getattr(inner, "dimensions", 0), getattr(inner, "model_name", "unknown")

    async def embed_batch(self, texts: list[str]):
        ok, minimize, reason = await _decide(self._inner, self._sf, self._tenant, self._purpose)
        if not ok:
            return None, EmbeddingCallOutcome(
                success=False, provider=self.name, model=self.model_name, latency_ms=0, input_count=len(texts),
                error_detail=f"{BLOCKED_PREFIX}:{reason}",
            )
        return await self._inner.embed_batch([scrub_text(t) for t in texts] if minimize else texts)


def bound_ai_provider(provider: AIProvider, session_factory, tenant_id: uuid.UUID | None, purpose: str) -> AIProvider:
    return provider if isinstance(provider, BoundedAIProvider) else BoundedAIProvider(provider, session_factory, tenant_id, purpose)


def bound_embedding_provider(provider: EmbeddingProvider, session_factory, tenant_id: uuid.UUID | None, purpose: str) -> EmbeddingProvider:
    return provider if isinstance(provider, BoundedEmbeddingProvider) else BoundedEmbeddingProvider(provider, session_factory, tenant_id, purpose)


def effective_provider_report() -> dict[str, Any]:
    """What runtime configuration ACTUALLY resolves to (class + connected flag), for the launch gate. Never includes keys."""
    from app.services.ai_provider import get_ai_provider
    from app.services.embedding_provider import get_embedding_provider

    ai, emb = get_ai_provider(), get_embedding_provider()
    return {
        "ai": {"class": type(ai).__name__, "name": ai.name, "external": not _is_local(ai) and ai.is_connected},
        "embedding": {"class": type(emb).__name__, "name": emb.name, "external": not _is_local(emb) and emb.is_connected},
        "external_allowlist_for_gated_tenants": sorted(allowed_external_providers()),
    }
