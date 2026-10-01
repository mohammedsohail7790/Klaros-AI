"""Phase 16a: dedicated unit tests for DiscoveryExtractionService and its
`_deterministic_fallback()` — closes the test gaps identified in
KLAROS_DISCOVERY_COMPLETION_AUDIT.md §14 items 1-2, 4-5 (this file adds
extraction-service-level coverage; the malformed/adversarial-provider-
response and full multi-turn-at-real-cap tests live alongside the existing
service-level suite in test_business_discovery_service.py, per that file's
established fixture pattern).

Exercises `_deterministic_fallback()` directly (a pure function, no DB
needed) and `DiscoveryExtractionService.extract()` against both a fake
"connected" provider (malformed output, invalid vocabulary, provider
failure) and the real `DeterministicAIProvider` (no key configured in the
test environment, matching every other Discovery test file's convention).
"""

import uuid

import pytest

from app.models.business_blueprint import BlueprintSectionKey
from app.services.ai_provider import AICallOutcome, AIErrorType, AIProvider
from app.services.discovery_extraction_service import (
    DiscoveryExtractionService,
    _deterministic_fallback,
)

# --- _deterministic_fallback(): pure-function tests, no DB -----------------


def test_fallback_initial_turn_proposes_identity_claim_and_first_question() -> None:
    result = _deterministic_fallback("A general business idea.", turn_sequence=0)
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.section_key == BlueprintSectionKey.IDENTITY.value
    assert claim.value == "A general business idea."
    assert claim.is_valid_vocabulary()
    assert result.follow_up_question is not None
    assert result.follow_up_section_key == BlueprintSectionKey.INDUSTRY.value
    assert result.exhausted is False


def test_fallback_second_turn_proposes_industry_claim_and_second_question() -> None:
    result = _deterministic_fallback("A consulting firm in the UK.", turn_sequence=1)
    assert len(result.claims) == 1
    assert result.claims[0].section_key == BlueprintSectionKey.INDUSTRY.value
    assert result.claims[0].is_valid_vocabulary()
    assert result.follow_up_question is not None
    assert result.follow_up_section_key == BlueprintSectionKey.BUSINESS_MODEL.value
    assert result.exhausted is False


def test_fallback_third_turn_proposes_business_model_claim_and_third_question() -> None:
    result = _deterministic_fallback("We charge a monthly subscription.", turn_sequence=2)
    assert len(result.claims) == 1
    assert result.claims[0].section_key == BlueprintSectionKey.BUSINESS_MODEL.value
    assert result.claims[0].is_valid_vocabulary()
    assert result.follow_up_question is not None
    assert result.follow_up_section_key == BlueprintSectionKey.REQUIRED_CAPABILITIES.value
    assert result.exhausted is False


def test_fallback_fourth_turn_proposes_required_capabilities_claim_and_exhausts() -> None:
    result = _deterministic_fallback("We need booking, payments and messaging.", turn_sequence=3)
    assert len(result.claims) == 1
    assert result.claims[0].section_key == BlueprintSectionKey.REQUIRED_CAPABILITIES.value
    assert result.claims[0].is_valid_vocabulary()
    assert result.follow_up_question is None
    assert result.follow_up_section_key is None
    assert result.exhausted is True


def test_fallback_beyond_sequence_is_a_harmless_exhausted_noop() -> None:
    """Defensive only — in practice the session completes at turn_sequence
    3 before a 5th call could ever happen (safety net; see
    business_discovery_service.py's own `at_cap`/`fallback_exhausted`
    completion logic)."""
    result = _deterministic_fallback("Anything.", turn_sequence=4)
    assert result.claims == []
    assert result.follow_up_question is None
    assert result.exhausted is True


def test_fallback_all_claims_are_fact_user_stated_and_proposed_by_construction() -> None:
    """Every fallback claim uses valid, existing vocabulary (Fact /
    USER_STATED) — no new claim_type or provenance is introduced, and
    nothing here sets a claim's status (status=PROPOSED is enforced later,
    at BusinessBlueprintService.propose_claim, tested in
    test_business_discovery_service.py::test_claims_are_proposed_not_confirmed)."""
    for seq in range(4):
        result = _deterministic_fallback("Some answer.", turn_sequence=seq)
        for claim in result.claims:
            assert claim.claim_type == "Fact"
            assert claim.provenance == "USER_STATED"


def test_fallback_never_branches_on_vertical_specific_content() -> None:
    """Cross-vertical neutrality at the extraction-service layer: identical
    fixed question text/section targeting regardless of what the user's
    free text says (KLAROS_DISCOVERY_COMPLETION_AUDIT.md's explicit
    no-vertical-branching requirement)."""
    medical = _deterministic_fallback("A medical tourism referral business connecting hospitals abroad.", turn_sequence=1)
    dropshipping = _deterministic_fallback("A dropshipping storefront selling home goods.", turn_sequence=1)
    hvac = _deterministic_fallback("An HVAC repair and installation company.", turn_sequence=1)
    assert medical.follow_up_question == dropshipping.follow_up_question == hvac.follow_up_question
    assert medical.follow_up_section_key == dropshipping.follow_up_section_key == hvac.follow_up_section_key
    assert medical.claims[0].section_key == dropshipping.claims[0].section_key == hvac.claims[0].section_key


# --- DiscoveryExtractionService.extract(): connected-provider edge cases ---


class _FakeProvider(AIProvider):
    """A minimal connected provider double — used only to exercise
    extract()'s validation/error-handling branches without a real network
    call or a real API key, matching this file's "isolated unit test"
    scope. Never touches _deterministic_fallback."""

    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, raw_text: str | None = None, success: bool = True, error_type: AIErrorType | None = None):
        self._raw_text = raw_text
        self._success = success
        self._error_type = error_type

    async def enrich_brief(self, *args, **kwargs):  # pragma: no cover - unused here
        raise NotImplementedError

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        if not self._success:
            return AICallOutcome(
                success=False,
                provider=self.name,
                model=self.model,
                latency_ms=1,
                raw_text=None,
                error_type=self._error_type or AIErrorType.PROVIDER_ERROR,
                error_detail="simulated failure",
            )
        return AICallOutcome(
            success=True,
            provider=self.name,
            model=self.model,
            latency_ms=1,
            raw_text=self._raw_text,
            error_type=None,
            error_detail=None,
        )


@pytest.fixture
def session_factory():
    from app.db.session import async_session_maker

    return async_session_maker


async def test_extract_falls_back_deterministically_when_provider_not_connected(session_factory) -> None:
    from app.services.ai_provider import get_ai_provider

    service = DiscoveryExtractionService(session_factory, get_ai_provider())
    assert service._provider.is_connected is False  # test-env convention, see test_business_discovery_service.py

    outcome = await service.extract(
        uuid.uuid4(), discovery_input="A business.", gap_keys=["INDUSTRY"], is_initial=True, actor_id=None, turn_sequence=0
    )
    assert outcome.available is True
    assert outcome.deterministic_fallback is True
    assert outcome.result.exhausted is False
    assert len(outcome.result.claims) == 1


async def test_extract_drops_out_of_vocabulary_claims_from_provider_response(session_factory) -> None:
    raw = (
        '{"claims": [{"claim_type": "Fact", "section_key": "IDENTITY", "key": "identity.description", '
        '"value": "x", "confidence": null, "provenance": "USER_STATED"}, '
        '{"claim_type": "NotARealType", "section_key": "NOT_A_SECTION", "key": "bad.key", '
        '"value": "y", "confidence": null, "provenance": "USER_STATED"}], '
        '"follow_up_question": "Q?", "follow_up_section_key": "INDUSTRY"}'
    )
    provider = _FakeProvider(raw_text=raw)
    service = DiscoveryExtractionService(session_factory, provider)

    outcome = await service.extract(
        uuid.uuid4(), discovery_input="x", gap_keys=["INDUSTRY"], is_initial=True, actor_id=None, turn_sequence=0
    )
    assert outcome.available is True
    assert len(outcome.result.claims) == 1  # the out-of-vocabulary claim was dropped
    assert outcome.result.claims[0].section_key == "IDENTITY"


async def test_extract_nulls_out_invalid_follow_up_section_key(session_factory) -> None:
    raw = '{"claims": [], "follow_up_question": "Q?", "follow_up_section_key": "NOT_A_REAL_SECTION"}'
    provider = _FakeProvider(raw_text=raw)
    service = DiscoveryExtractionService(session_factory, provider)

    outcome = await service.extract(
        uuid.uuid4(), discovery_input="x", gap_keys=["INDUSTRY"], is_initial=False, actor_id=None, turn_sequence=1
    )
    assert outcome.available is True
    assert outcome.result.follow_up_section_key is None
    assert outcome.result.follow_up_question is None


async def test_extract_handles_malformed_json_from_provider(session_factory) -> None:
    provider = _FakeProvider(raw_text="not valid json {{{")
    service = DiscoveryExtractionService(session_factory, provider)

    outcome = await service.extract(
        uuid.uuid4(), discovery_input="x", gap_keys=[], is_initial=False, actor_id=None, turn_sequence=1
    )
    assert outcome.available is False
    assert "malformed_response" in outcome.error_detail


async def test_extract_propagates_provider_failure_as_unavailable(session_factory) -> None:
    provider = _FakeProvider(success=False, error_type=AIErrorType.TIMEOUT)
    service = DiscoveryExtractionService(session_factory, provider)

    outcome = await service.extract(
        uuid.uuid4(), discovery_input="x", gap_keys=[], is_initial=False, actor_id=None, turn_sequence=1
    )
    assert outcome.available is False
    assert outcome.result is None
    assert "timeout" in outcome.error_detail


async def test_connected_provider_result_never_sets_exhausted(session_factory) -> None:
    """A connected provider's adaptive path never uses the fallback's
    `exhausted` signal — DiscoveryExtractionResult defaults it False and
    nothing in the provider-success path sets it, so a real provider's
    completion behavior (gap-driven or at_cap) is completely unchanged by
    Phase 16a."""
    raw = '{"claims": [], "follow_up_question": null, "follow_up_section_key": null}'
    provider = _FakeProvider(raw_text=raw)
    service = DiscoveryExtractionService(session_factory, provider)

    outcome = await service.extract(
        uuid.uuid4(), discovery_input="x", gap_keys=[], is_initial=False, actor_id=None, turn_sequence=7
    )
    assert outcome.available is True
    assert outcome.deterministic_fallback is False
    assert outcome.result.exhausted is False
