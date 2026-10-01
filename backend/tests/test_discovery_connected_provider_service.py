"""Phase 16b: connected-AI-provider contract tests for Business Discovery,
at the BusinessDiscoveryService/BusinessJourneyService level (not just the
DiscoveryExtractionService unit tests in test_discovery_extraction_service.py).

Phase 16a validated the deterministic (no-provider) fallback end to end.
This file validates the CONNECTED-provider path using a fake, in-process
`AIProvider` double (`is_connected=True`, no real network call) that
returns real, schema-valid `DiscoveryExtractionResult` JSON — exercising
exactly the same `DiscoveryExtractionService.extract()` /
`BusinessDiscoveryService._process_turn()` code a real Anthropic/OpenAI
provider would drive, without any live network dependency, so this suite
runs deterministically in CI and does not depend on network access or a
funded API key. Live-provider validation (an actual network call to a real
provider) is performed and reported separately — see
PHASE_16B_DISCOVERY_REAL_PROVIDER_VALIDATION_LOG.md.

No vertical-specific branching is introduced anywhere in this file or in
the fake provider — the "different business ideas produce different
structured output" tests below only prove the CONTRACT (identity claim
value maps to the given free text; the provider's returned claims and
question are correctly parsed/validated/persisted), never a business-type
conditional in Discovery's own code.
"""

import uuid

import pytest

from app.models.business_blueprint import BlueprintSectionKey, ClaimStatus
from app.models.business_discovery import DiscoverySessionStatus
from app.services.ai_provider import AICallOutcome, AIProvider
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.business_discovery_service import BusinessDiscoveryService
from app.services.business_journey_service import BusinessJourneyService
from app.services.discovery_extraction_service import DiscoveryExtractionService
from app.services.recommendation_service import RecommendationService

pytestmark = pytest.mark.asyncio


class _ScriptedConnectedProvider(AIProvider):
    """A fake CONNECTED provider (`is_connected=True`) that returns a
    scripted sequence of already-valid `DiscoveryExtractionResult` JSON
    strings, one per call, mimicking a real provider's adaptive,
    per-gap questioning without a live network call. Records every prompt
    it was given (for cross-tenant/data-boundary assertions) but performs
    no vertical branching of its own — the script is supplied by the test,
    never computed from business-type keywords.
    """

    is_connected = True
    name = "fake-connected"
    model = "fake-connected-model"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts_seen: list[str] = []

    async def enrich_brief(self, *args, **kwargs):  # pragma: no cover - unused here
        raise NotImplementedError

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.prompts_seen.append(prompt)
        idx = len(self.prompts_seen) - 1
        raw = self._responses[idx] if idx < len(self._responses) else self._responses[-1]
        return AICallOutcome(
            success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=raw,
        )


def _claim_json(section_key: str, key: str, value: str, claim_type: str = "Fact", provenance: str = "USER_STATED") -> dict:
    return {"claim_type": claim_type, "section_key": section_key, "key": key, "value": value, "confidence": None, "provenance": provenance}


def _result_json(claims: list[dict], follow_up_question: str | None, follow_up_section_key: str | None) -> str:
    import json

    return json.dumps({"claims": claims, "follow_up_question": follow_up_question, "follow_up_section_key": follow_up_section_key})


@pytest.fixture
def blueprint_service() -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


def _service_with_provider(blueprint_service: BusinessBlueprintService, provider: AIProvider) -> BusinessDiscoveryService:
    from app.db.session import async_session_maker

    extraction = DiscoveryExtractionService(async_session_maker, provider)
    return BusinessDiscoveryService(async_session_maker, blueprint_service, extraction)


# --- A/B/C: successful structured response, claim validation, exhausted=False ---


async def test_connected_provider_structured_response_produces_claims_and_next_question(
    blueprint_service: BusinessBlueprintService,
) -> None:
    provider = _ScriptedConnectedProvider(
        [
            _result_json(
                [_claim_json("IDENTITY", "identity.description", "A medical tourism referral business.")],
                "What countries do you place patients in, and which hospitals do you partner with?",
                "INDUSTRY",
            )
        ]
    )
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(
        tenant_id, business_idea="A medical tourism referral business.", created_by=None
    )

    assert result.extraction_available is True
    assert result.session.status == DiscoverySessionStatus.ACTIVE
    assert result.next_question == "What countries do you place patients in, and which hospitals do you partner with?"
    claims = await blueprint_service.list_claims(tenant_id, result.session.blueprint_id)
    assert len(claims) == 1
    assert claims[0].section_key == BlueprintSectionKey.IDENTITY.value
    assert claims[0].status == ClaimStatus.PROPOSED


async def test_connected_provider_invalid_claim_vocabulary_and_section_are_filtered(
    blueprint_service: BusinessBlueprintService,
) -> None:
    """A real provider's response can contain out-of-vocabulary claim_type/
    section_key/provenance, or an invalid follow_up_section_key — the
    existing DiscoveryExtractionService.extract() vocabulary guard (not
    modified by this phase) must still drop/null those before they ever
    reach BusinessBlueprintService.propose_claim."""
    raw = _result_json(
        [
            _claim_json("IDENTITY", "identity.description", "A subscription ecommerce business."),
            {
                "claim_type": "NotAType", "section_key": "NOT_A_SECTION", "key": "bad",
                "value": "x", "confidence": None, "provenance": "NOT_A_PROVENANCE",
            },
        ],
        "What products do you sell?",
        "NOT_A_REAL_SECTION_KEY",
    )
    provider = _ScriptedConnectedProvider([raw])
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(
        tenant_id, business_idea="A subscription ecommerce business.", created_by=None
    )

    claims = await blueprint_service.list_claims(tenant_id, result.session.blueprint_id)
    assert len(claims) == 1  # the malformed second claim was dropped
    assert claims[0].section_key == BlueprintSectionKey.IDENTITY.value
    # An invalid follow_up_section_key nulls out both fields (existing
    # extract() contract) — the turn simply asks nothing further this call.
    assert result.next_question is None


async def test_connected_provider_never_sets_exhausted_true_at_service_level(
    blueprint_service: BusinessBlueprintService,
) -> None:
    """Regression guard for the Phase 16a `exhausted` signal: a connected
    provider's DiscoveryExtractionResult never sets exhausted=True, so
    BusinessDiscoveryService's `fallback_exhausted` completion trigger
    (which requires BOTH deterministic_fallback AND exhausted) must never
    fire for a connected provider, even across many turns with no gap
    closure."""
    responses = [
        _result_json([_claim_json("IDENTITY", "identity.description", "A business.")], "Q1?", "INDUSTRY"),
        _result_json([_claim_json("INDUSTRY", "industry.x", "Retail, Canada.")], "Q2?", "BUSINESS_MODEL"),
        _result_json([_claim_json("BUSINESS_MODEL", "business_model.x", "Subscriptions.")], "Q3?", "REQUIRED_CAPABILITIES"),
    ]
    provider = _ScriptedConnectedProvider(responses)
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(tenant_id, business_idea="A business.", created_by=None)
    result = await service.submit_answer(tenant_id, result.session.id, answer="Retail, Canada.", actor_id=None)
    result = await service.submit_answer(tenant_id, result.session.id, answer="Subscriptions.", actor_id=None)

    # Never stalled (ACTIVE + next_question None) and never completed via
    # the fallback-only "exhausted" trigger — still ACTIVE, still asking.
    assert result.session.status == DiscoverySessionStatus.ACTIVE
    assert result.next_question == "Q3?"


# --- D: provider failure behavior (existing contract, verified not changed) ---


class _FailingConnectedProvider(AIProvider):
    is_connected = True
    name = "fake-failing"
    model = "fake-failing-model"

    def __init__(self, error_type, detail: str = "simulated failure") -> None:
        from app.services.ai_provider import AIErrorType

        self._error_type = error_type or AIErrorType.PROVIDER_ERROR
        self._detail = detail

    async def enrich_brief(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        return AICallOutcome(
            success=False, provider=self.name, model=self.model, latency_ms=1,
            raw_text=None, error_type=self._error_type, error_detail=self._detail,
        )


@pytest.mark.parametrize("error_type_name", ["TIMEOUT", "AUTHENTICATION", "RATE_LIMIT", "PROVIDER_ERROR", "NETWORK_ERROR"])
async def test_connected_provider_failure_leaves_session_active_with_raw_answer_persisted(
    blueprint_service: BusinessBlueprintService, error_type_name: str
) -> None:
    """Existing, unmodified contract (business_discovery_service.py
    `_process_turn`'s `if not outcome.available` branch): a provider
    failure of any classified type does NOT complete the session, does NOT
    silently fall back to the deterministic path, and does NOT lose the
    user's raw answer — it records `extraction_error` and leaves the
    session ACTIVE for a retry. This phase does not change this behavior;
    it only verifies it still holds for a connected provider."""
    from app.services.ai_provider import AIErrorType

    provider = _FailingConnectedProvider(AIErrorType[error_type_name])
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(tenant_id, business_idea="A business idea.", created_by=None)

    assert result.extraction_available is False
    assert result.extraction_error is not None
    assert error_type_name.lower() in result.extraction_error.lower()
    assert result.session.status == DiscoverySessionStatus.ACTIVE
    turns = await service.get_turns(tenant_id, result.session.id)
    assert turns[0].answer == "A business idea."  # raw answer never lost
    claims = await blueprint_service.list_claims(tenant_id, result.session.blueprint_id)
    assert claims == []  # no claim was fabricated from a failed call


async def test_connected_provider_malformed_json_response_leaves_session_active(
    blueprint_service: BusinessBlueprintService,
) -> None:
    provider = _ScriptedConnectedProvider(["not valid json {{{"])
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(tenant_id, business_idea="A business idea.", created_by=None)

    assert result.extraction_available is False
    assert "malformed_response" in result.extraction_error
    assert result.session.status == DiscoverySessionStatus.ACTIVE


# --- max_questions=8 enforcement for a connected, never-closing provider ---


async def test_connected_provider_completes_at_real_max_questions_cap_without_overshoot(
    blueprint_service: BusinessBlueprintService,
) -> None:
    """A connected provider that keeps asking follow-ups forever (never
    signals `follow_up_question=null`, and no claim is ever confirmed by a
    human) must still terminate cleanly at the real default
    max_questions=8 hard cap — proving a connected provider cannot drive
    questions_asked past the cap, and that the fallback-only `exhausted`
    trigger plays no part in this completion."""
    # 9 scripted turns of responses, each with a further follow-up — more
    # than the 8-question cap, to prove the cap (not exhaustion of the
    # script) is what stops it.
    responses = [
        _result_json([_claim_json("IDENTITY", f"identity.turn{i}", f"Answer {i}.")], f"Question {i+1}?", "INDUSTRY")
        for i in range(9)
    ]
    provider = _ScriptedConnectedProvider(responses)
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(tenant_id, business_idea="A business.", created_by=None)
    assert result.session.max_questions == 8

    turns_taken = 0
    while result.session.status != DiscoverySessionStatus.COMPLETED and turns_taken < 20:
        result = await service.submit_answer(tenant_id, result.session.id, answer=f"Answer {turns_taken}.", actor_id=None)
        turns_taken += 1
        assert not (result.session.status == DiscoverySessionStatus.ACTIVE and result.next_question is None)

    assert result.session.status == DiscoverySessionStatus.COMPLETED
    assert result.session.questions_asked == 8  # exactly the cap, never exceeded
    assert result.next_question is None


# --- proposed-only claim safety for the connected-provider path ------------


async def test_connected_provider_claims_remain_proposed_never_auto_confirmed(
    blueprint_service: BusinessBlueprintService,
) -> None:
    provider = _ScriptedConnectedProvider(
        [_result_json([_claim_json("IDENTITY", "identity.description", "A business.")], "Q?", "INDUSTRY")]
    )
    service = _service_with_provider(blueprint_service, provider)
    tenant_id = uuid.uuid4()

    result = await service.start_session(tenant_id, business_idea="A business.", created_by=None)
    claims = await blueprint_service.list_claims(tenant_id, result.session.blueprint_id)
    assert len(claims) == 1
    assert all(c.status == ClaimStatus.PROPOSED for c in claims)


# --- adaptive questioning contract: two differently-shaped business ideas --


async def test_connected_provider_produces_different_structured_output_for_different_ideas(
    blueprint_service: BusinessBlueprintService,
) -> None:
    """Contract-only test (per task instructions, never asserting exact
    LLM wording): two different business ideas fed through the SAME
    generic Discovery code path produce independently correct, differently
    -shaped structured extraction results — proving the pipeline carries
    each tenant's own idea through to its own claim value/question/section,
    with no hardcoded business-type branching in Discovery's own code
    (only the test's fake provider script differs, which stands in for a
    real model's own genuinely adaptive response)."""
    idea_a = "A medical tourism company connecting international patients with hospitals in Turkey."
    idea_b = "A subscription-based ecommerce business selling specialized products online."

    provider_a = _ScriptedConnectedProvider(
        [_result_json([_claim_json("IDENTITY", "identity.description", idea_a)], "Which countries and hospitals?", "INDUSTRY")]
    )
    provider_b = _ScriptedConnectedProvider(
        [_result_json([_claim_json("IDENTITY", "identity.description", idea_b)], "What is the subscription cadence?", "BUSINESS_MODEL")]
    )
    service_a = _service_with_provider(blueprint_service, provider_a)
    service_b = _service_with_provider(blueprint_service, provider_b)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    result_a = await service_a.start_session(tenant_a, business_idea=idea_a, created_by=None)
    result_b = await service_b.start_session(tenant_b, business_idea=idea_b, created_by=None)

    claims_a = await blueprint_service.list_claims(tenant_a, result_a.session.blueprint_id)
    claims_b = await blueprint_service.list_claims(tenant_b, result_b.session.blueprint_id)

    assert claims_a[0].value == idea_a
    assert claims_b[0].value == idea_b
    assert result_a.next_question != result_b.next_question
    # Section targeting genuinely differs per business context (contract,
    # not wording) — idea A's next gap is INDUSTRY, idea B's is BUSINESS_MODEL.
    turns_a = await service_a.get_turns(tenant_a, result_a.session.id)
    turns_b = await service_b.get_turns(tenant_b, result_b.session.id)
    assert turns_a[0].question == "Which countries and hospitals?"
    assert turns_b[0].question == "What is the subscription cadence?"


# --- cross-tenant isolation: tenant A's data must never reach tenant B's prompt ---


async def test_cross_tenant_discovery_context_never_leaks_into_another_tenants_prompt(
    blueprint_service: BusinessBlueprintService,
) -> None:
    """Tenant A's business idea/answers must never appear in the prompt
    built for Tenant B's session, and vice versa, even when both tenants
    are driven through the SAME DiscoveryExtractionService/provider
    instance (the realistic case — one shared AIProvider serves all
    tenants; only the per-call `discovery_input`/`gap_keys` differ)."""
    shared_provider = _ScriptedConnectedProvider(
        [
            _result_json([_claim_json("IDENTITY", "identity.description", "TENANT_A_SECRET_IDEA")], "Qa?", "INDUSTRY"),
            _result_json([_claim_json("IDENTITY", "identity.description", "TENANT_B_SECRET_IDEA")], "Qb?", "INDUSTRY"),
        ]
    )
    service = _service_with_provider(blueprint_service, shared_provider)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    result_a = await service.start_session(tenant_a, business_idea="TENANT_A_SECRET_IDEA", created_by=None)
    result_b = await service.start_session(tenant_b, business_idea="TENANT_B_SECRET_IDEA", created_by=None)

    assert len(shared_provider.prompts_seen) == 2
    prompt_for_a, prompt_for_b = shared_provider.prompts_seen
    assert "TENANT_A_SECRET_IDEA" in prompt_for_a
    assert "TENANT_B_SECRET_IDEA" not in prompt_for_a
    assert "TENANT_B_SECRET_IDEA" in prompt_for_b
    assert "TENANT_A_SECRET_IDEA" not in prompt_for_b

    # Application-level tenant scoping is unchanged: tenant B cannot read
    # tenant A's session/claims through the service layer either.
    from app.services.business_discovery_service import DiscoverySessionNotFoundError

    with pytest.raises(DiscoverySessionNotFoundError):
        await service.get_session(tenant_b, result_a.session.id)
    with pytest.raises(DiscoverySessionNotFoundError):
        await service.get_session(tenant_a, result_b.session.id)

    claims_a = await blueprint_service.list_claims(tenant_a, result_a.session.blueprint_id)
    claims_b = await blueprint_service.list_claims(tenant_b, result_b.session.blueprint_id)
    assert claims_a[0].value == "TENANT_A_SECRET_IDEA"
    assert claims_b[0].value == "TENANT_B_SECRET_IDEA"


# --- Business Journey compatibility: connected-provider completion is a ----
# transparent DiscoverySessionStatus.COMPLETED to BusinessJourneyService ----


async def test_connected_provider_completion_via_cap_advances_business_journey(
    blueprint_service: BusinessBlueprintService,
) -> None:
    """BusinessJourneyService.complete_discovery only checks
    DiscoverySessionStatus.COMPLETED (business_journey_service.py:262) — it
    is not touched by this phase. This proves a connected-provider session
    that completes via the max_questions cap (never via gap-closure, since
    no claim is ever confirmed here) is still a fully legitimate
    COMPLETED for the unchanged Business Journey checkpoint."""
    from app.db.session import async_session_maker

    responses = [
        _result_json([_claim_json("IDENTITY", f"identity.turn{i}", f"Answer {i}.")], f"Question {i+1}?", "INDUSTRY")
        for i in range(9)
    ]
    provider = _ScriptedConnectedProvider(responses)
    discovery_service = _service_with_provider(blueprint_service, provider)
    journey_service = BusinessJourneyService(
        async_session_maker, discovery_service, blueprint_service, RecommendationService(async_session_maker)
    )
    tenant_id = uuid.uuid4()

    journey_result = await journey_service.start_journey(tenant_id, business_idea="A business.", created_by=None)
    journey = journey_result.journey
    assert str(journey.status).endswith("DISCOVERY_ACTIVE")

    session_state = await discovery_service.get_session(tenant_id, journey.discovery_session_id)
    turns_taken = 0
    while session_state.status != DiscoverySessionStatus.COMPLETED and turns_taken < 20:
        turn_result = await discovery_service.submit_answer(
            tenant_id, journey.discovery_session_id, answer=f"Answer {turns_taken}.", actor_id=None
        )
        session_state = turn_result.session
        turns_taken += 1

    assert session_state.status == DiscoverySessionStatus.COMPLETED
    assert session_state.questions_asked == 8

    advanced = await journey_service.complete_discovery(tenant_id, journey.id, actor_id=None)
    assert str(advanced.journey.status).endswith("BLUEPRINT_REVIEW")
