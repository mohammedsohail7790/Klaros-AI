"""Phase 2: BusinessDiscoveryService — session lifecycle, turn persistence,
deterministic-fallback extraction (no AI key configured in the test
environment — see app/services/ai_provider.py's get_ai_provider()), gap-
driven completion, tenant isolation, question cap.
"""

import uuid

import pytest

from app.models.business_blueprint import BlueprintSectionKey, BlueprintSectionStatus, ClaimStatus
from app.models.business_discovery import DiscoverySessionStatus, DiscoveryTurnKind
from app.services.ai_provider import get_ai_provider
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.business_discovery_service import (
    BusinessDiscoveryService,
    DiscoverySessionCompletedError,
    DiscoverySessionNotFoundError,
)
from app.services.discovery_extraction_service import DiscoveryExtractionService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def blueprint_service(tool_registry) -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


@pytest.fixture
def service(blueprint_service: BusinessBlueprintService) -> BusinessDiscoveryService:
    from app.db.session import async_session_maker

    extraction = DiscoveryExtractionService(async_session_maker, get_ai_provider())
    return BusinessDiscoveryService(async_session_maker, blueprint_service, extraction)


async def test_ai_provider_is_deterministic_in_test_env() -> None:
    # Confirms this test file is honestly exercising the "no AI key
    # configured" degrade path, not silently skipping it.
    assert get_ai_provider().is_connected is False


async def test_start_session_persists_initial_turn_and_proposes_a_fact(service: BusinessDiscoveryService) -> None:
    tenant_id = uuid.uuid4()
    result = await service.start_session(
        tenant_id, business_idea="We connect patients with hospitals in Turkey and India.", created_by=None
    )
    assert result.session.status == DiscoverySessionStatus.ACTIVE
    assert result.turn.kind == DiscoveryTurnKind.INITIAL_DESCRIPTION
    assert result.turn.answer == "We connect patients with hospitals in Turkey and India."
    assert result.extraction_available is True
    assert len(result.proposed_claim_ids) == 1  # deterministic fallback: one IDENTITY.description Fact
    assert result.next_question is not None


async def test_raw_answer_always_persisted_even_when_extraction_unavailable(
    service: BusinessDiscoveryService,
) -> None:
    """Deterministic fallback IS "available" (never fails) — this proves
    the raw turn survives regardless."""
    tenant_id = uuid.uuid4()
    result = await service.start_session(tenant_id, business_idea="A small consulting firm.", created_by=None)
    turns = await service.get_turns(tenant_id, result.session.id)
    assert len(turns) == 1
    assert turns[0].answer == "A small consulting firm."


async def test_submit_answer_unknown_session_raises(service: BusinessDiscoveryService) -> None:
    with pytest.raises(DiscoverySessionNotFoundError):
        await service.submit_answer(uuid.uuid4(), uuid.uuid4(), answer="x", actor_id=None)


async def test_tenant_isolation_get_session(service: BusinessDiscoveryService) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    result = await service.start_session(tenant_a, business_idea="A dropshipping storefront.", created_by=None)
    with pytest.raises(DiscoverySessionNotFoundError):
        await service.get_session(tenant_b, result.session.id)


async def test_session_completes_once_question_cap_reached(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    tenant_id = uuid.uuid4()
    result = await service.start_session(tenant_id, business_idea="A general business.", created_by=None)

    async with __import__("app.db.session", fromlist=["async_session_maker"]).async_session_maker() as session:
        from app.models.business_discovery import DiscoverySession

        row = await session.get(DiscoverySession, result.session.id)
        row.max_questions = 1
        await session.commit()

    final = await service.submit_answer(tenant_id, result.session.id, answer="Another detail.", actor_id=None)
    assert final.session.status == DiscoverySessionStatus.COMPLETED
    assert final.next_question is None


async def test_cannot_submit_answer_to_completed_session(service: BusinessDiscoveryService) -> None:
    tenant_id = uuid.uuid4()
    result = await service.start_session(tenant_id, business_idea="A business.", created_by=None)

    async with __import__("app.db.session", fromlist=["async_session_maker"]).async_session_maker() as session:
        from app.models.business_discovery import DiscoverySession

        row = await session.get(DiscoverySession, result.session.id)
        row.status = DiscoverySessionStatus.COMPLETED
        await session.commit()

    with pytest.raises(DiscoverySessionCompletedError):
        await service.submit_answer(tenant_id, result.session.id, answer="too late", actor_id=None)


async def test_claims_are_proposed_not_confirmed(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    """AI/deterministic-derived claims must never be silently treated as
    user-confirmed facts (this task's explicit provenance requirement)."""
    tenant_id = uuid.uuid4()
    result = await service.start_session(tenant_id, business_idea="A medical tourism referral business.", created_by=None)
    claims = await blueprint_service.list_claims(tenant_id, result.session.blueprint_id)
    assert len(claims) >= 1
    assert all(c.status == ClaimStatus.PROPOSED for c in claims)


async def test_gap_check_uses_confirmed_claims_only(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    """A still-PROPOSED claim does not close a minimum-bar gap — human
    confirmation is required before a section counts as COMPLETE."""
    tenant_id = uuid.uuid4()
    result = await service.start_session(tenant_id, business_idea="A business.", created_by=None)
    sections = await blueprint_service.list_sections(tenant_id, result.session.blueprint_id)
    identity = next(s for s in sections if s.section_key == BlueprintSectionKey.IDENTITY.value)
    assert identity.status != BlueprintSectionStatus.COMPLETE


# --- Phase 16a: deterministic fallback completion (KLAROS_DISCOVERY_ -------
# COMPLETION_AUDIT.md) — multi-turn exhaustion at the REAL default
# max_questions=8, not a rigged max_questions=1. These directly close audit
# §14 gap 1: "no test drives submit_answer repeatedly with the real
# max_questions=8 default".


async def test_second_turn_proposes_industry_claim_and_returns_next_question(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    tenant_id = uuid.uuid4()
    start = await service.start_session(tenant_id, business_idea="A general business.", created_by=None)
    assert start.session.max_questions == 8  # real default, not rigged down

    second = await service.submit_answer(
        tenant_id, start.session.id, answer="A consulting firm operating in the UK.", actor_id=None
    )
    assert second.session.status == DiscoverySessionStatus.ACTIVE
    assert second.next_question is not None
    assert len(second.proposed_claim_ids) == 1

    claims = await blueprint_service.list_claims(tenant_id, start.session.blueprint_id)
    section_keys = {c.section_key for c in claims}
    assert BlueprintSectionKey.INDUSTRY.value in section_keys


async def test_third_turn_proposes_business_model_claim_and_returns_next_question(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    tenant_id = uuid.uuid4()
    start = await service.start_session(tenant_id, business_idea="A general business.", created_by=None)
    await service.submit_answer(tenant_id, start.session.id, answer="A consulting firm in the UK.", actor_id=None)
    third = await service.submit_answer(
        tenant_id, start.session.id, answer="We charge a monthly retainer.", actor_id=None
    )
    assert third.session.status == DiscoverySessionStatus.ACTIVE
    assert third.next_question is not None

    claims = await blueprint_service.list_claims(tenant_id, start.session.blueprint_id)
    section_keys = {c.section_key for c in claims}
    assert BlueprintSectionKey.BUSINESS_MODEL.value in section_keys


async def test_fourth_turn_proposes_required_capabilities_claim_with_valid_vocabulary(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    tenant_id = uuid.uuid4()
    start = await service.start_session(tenant_id, business_idea="A general business.", created_by=None)
    await service.submit_answer(tenant_id, start.session.id, answer="A consulting firm in the UK.", actor_id=None)
    await service.submit_answer(tenant_id, start.session.id, answer="We charge a monthly retainer.", actor_id=None)
    fourth = await service.submit_answer(
        tenant_id, start.session.id, answer="We need scheduling, invoicing and email.", actor_id=None
    )

    claims = await blueprint_service.list_claims(tenant_id, start.session.blueprint_id)
    section_keys = {c.section_key for c in claims}
    assert BlueprintSectionKey.REQUIRED_CAPABILITIES.value in section_keys
    assert all(
        c.claim_type in {"Fact", "Inference", "Assumption", "Requirement", "Preference", "Constraint", "Decision", "Unknown"}
        for c in claims
    )
    assert all(c.provenance in {"USER_STATED", "AI_INFERRED", "SYSTEM_DEFAULT"} for c in claims)
    # Exhaustion happens exactly on this turn (see next test) — this turn
    # itself just proves the claim/vocabulary side of it.
    assert fourth.session.questions_asked == 3


async def test_deterministic_fallback_exhaustion_completes_session_without_stalling(
    service: BusinessDiscoveryService,
) -> None:
    """The core Phase 16a fix: the deterministic fallback must reach a
    genuine COMPLETED state — never an ACTIVE session with
    next_question=None (the exact defect KLAROS_DISCOVERY_COMPLETION_
    AUDIT.md documented as an indefinite UI stall)."""
    tenant_id = uuid.uuid4()
    start = await service.start_session(tenant_id, business_idea="A general business.", created_by=None)
    r1 = await service.submit_answer(tenant_id, start.session.id, answer="A consulting firm in the UK.", actor_id=None)
    r2 = await service.submit_answer(tenant_id, start.session.id, answer="We charge a monthly retainer.", actor_id=None)
    r3 = await service.submit_answer(
        tenant_id, start.session.id, answer="We need scheduling, invoicing and email.", actor_id=None
    )

    # No intermediate turn is ever an ACTIVE/next_question=None stall.
    for r in (start, r1, r2):
        assert not (r.session.status == DiscoverySessionStatus.ACTIVE and r.next_question is None)

    assert r3.session.status == DiscoverySessionStatus.COMPLETED
    assert r3.next_question is None
    assert r3.session.questions_asked <= r3.session.max_questions
    assert r3.session.questions_asked == 3  # well under the 8 hard cap


async def test_deterministic_fallback_end_to_end_never_exceeds_real_max_questions_cap(
    service: BusinessDiscoveryService,
) -> None:
    """Drives the fallback through to COMPLETED using the real, un-rigged
    default max_questions=8 (audit §14 gap 1) — proves no stall and no
    overshoot of the hard cap."""
    tenant_id = uuid.uuid4()
    result = await service.start_session(tenant_id, business_idea="A generic small business.", created_by=None)
    assert result.session.max_questions == 8

    answers = [
        "It operates in the retail sector, based in Canada.",
        "Revenue comes from one-time product sales.",
        "It needs point-of-sale, inventory and basic accounting tools.",
    ]
    turns_taken = 0
    for answer in answers:
        if result.session.status == DiscoverySessionStatus.COMPLETED:
            break
        result = await service.submit_answer(tenant_id, result.session.id, answer=answer, actor_id=None)
        turns_taken += 1
        assert not (result.session.status == DiscoverySessionStatus.ACTIVE and result.next_question is None)

    assert result.session.status == DiscoverySessionStatus.COMPLETED
    assert result.session.questions_asked <= 8
    assert turns_taken <= 8


async def test_claims_remain_proposed_through_full_deterministic_completion(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    """Fallback-generated claims are never auto-confirmed, even once the
    session legitimately reaches COMPLETED — the Blueprint review human
    checkpoint is unchanged (KLAROS_DISCOVERY_COMPLETION_AUDIT.md §9)."""
    tenant_id = uuid.uuid4()
    start = await service.start_session(tenant_id, business_idea="A general business.", created_by=None)
    await service.submit_answer(tenant_id, start.session.id, answer="A consulting firm in the UK.", actor_id=None)
    await service.submit_answer(tenant_id, start.session.id, answer="We charge a monthly retainer.", actor_id=None)
    final = await service.submit_answer(
        tenant_id, start.session.id, answer="We need scheduling, invoicing and email.", actor_id=None
    )
    assert final.session.status == DiscoverySessionStatus.COMPLETED

    claims = await blueprint_service.list_claims(tenant_id, start.session.blueprint_id)
    assert len(claims) == 4  # IDENTITY, INDUSTRY, BUSINESS_MODEL, REQUIRED_CAPABILITIES
    assert all(c.status == ClaimStatus.PROPOSED for c in claims)


async def test_deterministic_fallback_completion_is_vertical_neutral(
    service: BusinessDiscoveryService, blueprint_service: BusinessBlueprintService
) -> None:
    """Cross-vertical neutrality (audit-required): a medical-tourism-shaped
    and a dropshipping-shaped business idea both complete via the identical
    generic fixed sequence — same section keys, same turn count, no
    vertical branch anywhere in the fallback path."""
    idea_by_vertical = {
        "medical": "We connect international patients with partner hospitals abroad.",
        "dropshipping": "We run a dropshipping storefront selling home goods online.",
    }
    outcomes = {}
    for vertical, idea in idea_by_vertical.items():
        tenant_id = uuid.uuid4()
        result = await service.start_session(tenant_id, business_idea=idea, created_by=None)
        result = await service.submit_answer(tenant_id, result.session.id, answer="Answer one.", actor_id=None)
        result = await service.submit_answer(tenant_id, result.session.id, answer="Answer two.", actor_id=None)
        result = await service.submit_answer(tenant_id, result.session.id, answer="Answer three.", actor_id=None)
        claims = await blueprint_service.list_claims(tenant_id, result.session.blueprint_id)
        outcomes[vertical] = (result.session.status, result.session.questions_asked, sorted(c.section_key for c in claims))

    medical_outcome = outcomes["medical"]
    dropshipping_outcome = outcomes["dropshipping"]
    assert medical_outcome == dropshipping_outcome
    assert medical_outcome[0] == DiscoverySessionStatus.COMPLETED
