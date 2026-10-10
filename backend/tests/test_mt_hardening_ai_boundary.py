"""AI-provider boundary for consent-gated (Medical Tourism) tenants: nothing leaves the process to an external AI / embedding provider unless the
provider is explicitly allowlisted; unknown status or unknown tenant fails closed; allowlisted calls are minimised; non-gated tenants are unchanged;
and every call site goes through the boundary (static guard)."""
from __future__ import annotations

import pathlib
import re
import uuid

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.services import ai_boundary
from app.services.ai_provider import AICallOutcome, AIProvider, DeterministicAIProvider
from app.services.embedding_provider import DeterministicEmbeddingProvider, EmbeddingCallOutcome, EmbeddingProvider
from tests.test_consent_gate_intake import _gated, _plain
from tests.test_halla_integration import _tenant_id, halla  # noqa: F401


class ExternalAI(AIProvider):
    is_connected, name, model = True, "fake-external", "m"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.prompts.append(prompt)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text="{}")

    async def enrich_brief(self, headline, insights, *, brand_voice=None, company_memory=None):
        self.prompts.append(headline + " " + str(insights))
        return None, None


class ExternalEmbed(EmbeddingProvider):
    is_connected, name, model_name, dimensions = True, "fake-embed", "e", 4

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed_batch(self, texts):
        self.texts += texts
        return [[0.0] * 4 for _ in texts], EmbeddingCallOutcome(success=True, provider=self.name, model=self.model_name, latency_ms=1, input_count=len(texts))


PROMPT = "Patient Jane, mail jane.doe@example.com, phone +1 555 123 4567, needs a liver transplant"


@pytest.fixture
def allowlist(monkeypatch):
    def set_(value: str) -> None:
        monkeypatch.setattr(get_settings(), "AI_EXTERNAL_PROCESSING_ALLOWED_PROVIDERS", value)

    return set_


async def test_gated_tenant_data_never_reaches_an_unlisted_external_provider(client, halla, allowlist) -> None:  # noqa: F811
    _, tid = await _gated(client, "AI Gate", "aigate@example.com")
    ext = ExternalAI()
    allowlist("")                                            # the default: nothing allowed
    out = await ai_boundary.bound_ai_provider(ext, async_session_maker, tid, "t").generate_structured(PROMPT)
    assert out.success is False and ai_boundary.BLOCKED_PREFIX in (out.error_detail or "") and ext.prompts == []
    res, outcome = await ai_boundary.bound_ai_provider(ext, async_session_maker, tid, "t").enrich_brief("h", [{"summary": PROMPT}])
    assert res is None and ext.prompts == [] and ai_boundary.BLOCKED_PREFIX in (outcome.error_detail or "")
    allowlist("some-other-provider")
    assert (await ai_boundary.bound_ai_provider(ext, async_session_maker, tid, "t").generate_structured(PROMPT)).success is False and ext.prompts == []


async def test_an_allowlisted_provider_receives_a_minimised_prompt_for_a_gated_tenant(client, halla, allowlist) -> None:  # noqa: F811
    _, tid = await _gated(client, "AI Gate 2", "aigate2@example.com")
    ext = ExternalAI()
    allowlist("Fake-External")                               # case-insensitive
    out = await ai_boundary.bound_ai_provider(ext, async_session_maker, tid, "t").generate_structured(PROMPT)
    assert out.success and len(ext.prompts) == 1
    assert "jane.doe@example.com" not in ext.prompts[0] and "123 4567" not in ext.prompts[0]
    emb = ExternalEmbed()
    allowlist("fake-embed")
    vectors, _ = await ai_boundary.bound_embedding_provider(emb, async_session_maker, tid, "t").embed_batch([PROMPT])
    assert vectors is not None and "jane.doe@example.com" not in emb.texts[0]


async def test_unknown_status_and_unknown_tenant_fail_closed_and_local_providers_always_pass(client, halla, allowlist) -> None:  # noqa: F811
    allowlist("")
    ext = ExternalAI()

    def broken():
        raise RuntimeError("db down")

    assert (await ai_boundary.bound_ai_provider(ext, broken, uuid.uuid4(), "t").generate_structured(PROMPT)).success is False
    assert (await ai_boundary.bound_ai_provider(ext, async_session_maker, None, "t").generate_structured(PROMPT)).success is False
    assert ext.prompts == []
    # a provider verified (by class) to make no external call is never blocked; a subclass could, so it is NOT trusted by name
    det = ai_boundary.bound_ai_provider(DeterministicAIProvider(), broken, None, "t")
    assert (await det.generate_structured(PROMPT)).error_detail and ai_boundary.BLOCKED_PREFIX not in det_detail(await det.generate_structured(PROMPT))
    vec, _ = await ai_boundary.bound_embedding_provider(DeterministicEmbeddingProvider(), broken, None, "t").embed_batch(["x"])
    assert vec is not None

    class Sneaky(DeterministicAIProvider):
        name = "deterministic"

        async def generate_structured(self, prompt):
            ext.prompts.append(prompt)
            return await super().generate_structured(prompt)

    assert (await ai_boundary.bound_ai_provider(Sneaky(), broken, None, "t").generate_structured(PROMPT)).error_detail.startswith(ai_boundary.BLOCKED_PREFIX)


def det_detail(outcome) -> str:
    return outcome.error_detail or ""


async def test_non_gated_tenants_are_unchanged(client, halla, allowlist) -> None:  # noqa: F811
    token, _ = await _plain(client, "AI Plain", "aiplain@example.com")
    tid = await _tenant_id(client, token)
    allowlist("")
    ext = ExternalAI()
    assert (await ai_boundary.bound_ai_provider(ext, async_session_maker, tid, "t").generate_structured(PROMPT)).success is True
    assert ext.prompts == [PROMPT]                           # byte-for-byte, no minimisation


async def test_qualification_of_a_gated_lead_does_not_call_the_external_provider(client, halla, allowlist) -> None:  # noqa: F811
    from app.models.actor import ActorType
    from app.services.ai_qualification_service import AIQualificationService
    from tests.test_consent_gate_channels import _attested_lead

    token, tid = await _gated(client, "AI Qual", "aiqual@example.com")
    lead_id = await _attested_lead(client, token)
    allowlist("")
    ext = ExternalAI()
    result = await AIQualificationService(async_session_maker, ext).generate_recommendation(
        tid, uuid.UUID(lead_id), actor_type=ActorType.SYSTEM, actor_id=None
    )
    assert ext.prompts == []                                  # the lead's content never reached the external provider
    assert result.available is False


def test_effective_provider_report_reflects_runtime_configuration(monkeypatch) -> None:
    rep = ai_boundary.effective_provider_report()
    assert rep["ai"]["class"] == "DeterministicAIProvider" and rep["ai"]["external"] is False
    assert rep["external_allowlist_for_gated_tenants"] == []
    assert "key" not in str(rep).lower()


def test_every_model_call_site_goes_through_the_boundary() -> None:
    """Static guard: a direct provider call that is not wrapped by bound_*_provider(...) would bypass the allowlist."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed_files = {"ai_boundary.py", "ai_provider.py", "embedding_provider.py"}
    call = re.compile(r"\.(generate_structured|enrich_brief|embed_batch|embed)\(")
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in allowed_files:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if call.search(line) and "await" in line and not line.lstrip().startswith(("#", '"', "'", "`")) and "def " not in line and "bound_" not in line:
                offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()[:90]}")
    # ai_content_service receives an already-bound provider from content_service / seo_service (both wrap it, asserted below)
    offenders = [o for o in offenders if not o.startswith("services/ai_content_service.py")]
    assert offenders == []
    for name in ("content_service.py", "seo_service.py"):
        assert "bound_ai_provider(" in (root / "services" / name).read_text()
