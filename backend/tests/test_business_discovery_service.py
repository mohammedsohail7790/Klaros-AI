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
