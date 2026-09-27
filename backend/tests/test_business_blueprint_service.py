"""Phase 2: BusinessBlueprintService — creation, section/claim lifecycle,
minimum-bar activation, tenant isolation, versioning-on-edit.
"""

import uuid

import pytest

from app.models.business_blueprint import (
    BlueprintSectionKey,
    BlueprintSectionStatus,
    BlueprintStatus,
    ClaimProvenance,
    ClaimStatus,
    ClaimType,
)
from app.services.business_blueprint_service import (
    BlueprintActivationError,
    BlueprintNotFoundError,
    BusinessBlueprintService,
    ClaimNotFoundError,
    InvalidClaimTransitionError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tool_registry) -> BusinessBlueprintService:
    from app.db.session import async_session_maker

    return BusinessBlueprintService(async_session_maker)


async def test_get_or_create_draft_creates_all_sections(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    assert blueprint.status == BlueprintStatus.DRAFT
    assert blueprint.version == 1

    sections = await service.list_sections(tenant_id, blueprint.id)
    assert {s.section_key for s in sections} == {k.value for k in BlueprintSectionKey}
    assert all(s.status == BlueprintSectionStatus.EMPTY for s in sections)


async def test_get_or_create_draft_is_idempotent(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    first = await service.get_or_create_draft(tenant_id, created_by=None)
    second = await service.get_or_create_draft(tenant_id, created_by=None)
    assert first.id == second.id


async def test_tenant_isolation_get_or_create_draft(service: BusinessBlueprintService) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    blueprint_a = await service.get_or_create_draft(tenant_a, created_by=None)
    blueprint_b = await service.get_or_create_draft(tenant_b, created_by=None)
    assert blueprint_a.id != blueprint_b.id

    with pytest.raises(BlueprintNotFoundError):
        await service.get_by_id(tenant_b, blueprint_a.id)


async def test_propose_confirm_claim_recomputes_section(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)

    claim = await service.propose_claim(
        tenant_id,
        blueprint.id,
        section_key=BlueprintSectionKey.IDENTITY.value,
        claim_type=ClaimType.FACT.value,
        key="identity.description",
        value="A referral brokerage connecting patients with hospitals abroad.",
        confidence=None,
        provenance=ClaimProvenance.USER_STATED.value,
        discovery_turn_id=None,
        evidence_ref=None,
    )
    assert claim.status == ClaimStatus.PROPOSED

    sections = await service.list_sections(tenant_id, blueprint.id)
    identity = next(s for s in sections if s.section_key == BlueprintSectionKey.IDENTITY.value)
    assert identity.status == BlueprintSectionStatus.DRAFT  # proposed only, not yet confirmed

    confirmed = await service.confirm_claim(tenant_id, claim.id, confirmed_by=uuid.uuid4())
    assert confirmed.status == ClaimStatus.CONFIRMED
    assert confirmed.confirmed_at is not None

    sections = await service.list_sections(tenant_id, blueprint.id)
    identity = next(s for s in sections if s.section_key == BlueprintSectionKey.IDENTITY.value)
    assert identity.status == BlueprintSectionStatus.COMPLETE
    assert identity.data["identity.description"] == claim.value


async def test_confirm_claim_mirrors_into_company_memory(service: BusinessBlueprintService) -> None:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.company_memory import CompanyMemory, MemorySource, MemoryStatus

    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    claim = await service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.IDENTITY.value,
        claim_type=ClaimType.FACT.value, key="identity.description", value="A test business.",
        confidence=None, provenance=ClaimProvenance.USER_STATED.value,
        discovery_turn_id=None, evidence_ref=None,
    )
    confirmer = uuid.uuid4()
    await service.confirm_claim(tenant_id, claim.id, confirmed_by=confirmer)

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(CompanyMemory).where(
                    CompanyMemory.tenant_id == tenant_id, CompanyMemory.source_entity_id == claim.id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    mirrored = rows[0]
    assert mirrored.status == MemoryStatus.ACTIVE
    assert mirrored.source == MemorySource.OWNER_EXPLICIT
    assert mirrored.value == "A test business."
    assert mirrored.source_entity_type == "blueprint_claim"


async def test_confirm_claim_is_idempotent(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    claim = await service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.GOALS.value,
        claim_type=ClaimType.FACT.value, key="goals.primary", value="grow revenue",
        confidence=None, provenance=ClaimProvenance.USER_STATED.value,
        discovery_turn_id=None, evidence_ref=None,
    )
    first = await service.confirm_claim(tenant_id, claim.id, confirmed_by=None)
    second = await service.confirm_claim(tenant_id, claim.id, confirmed_by=None)
    assert first.id == second.id
    assert second.status == ClaimStatus.CONFIRMED


async def test_cannot_confirm_a_rejected_claim(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    claim = await service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.GOALS.value,
        claim_type=ClaimType.FACT.value, key="goals.primary", value="grow revenue",
        confidence=None, provenance=ClaimProvenance.USER_STATED.value,
        discovery_turn_id=None, evidence_ref=None,
    )
    await service.reject_claim(tenant_id, claim.id, rejected_by=None, reason="wrong")
    with pytest.raises(InvalidClaimTransitionError):
        await service.confirm_claim(tenant_id, claim.id, confirmed_by=None)


async def test_cannot_reject_a_confirmed_claim(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    claim = await service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.GOALS.value,
        claim_type=ClaimType.FACT.value, key="goals.primary", value="grow revenue",
        confidence=None, provenance=ClaimProvenance.USER_STATED.value,
        discovery_turn_id=None, evidence_ref=None,
    )
    await service.confirm_claim(tenant_id, claim.id, confirmed_by=None)
    with pytest.raises(InvalidClaimTransitionError):
        await service.reject_claim(tenant_id, claim.id, rejected_by=None, reason="too late")


async def test_confirm_unknown_claim_raises(service: BusinessBlueprintService) -> None:
    with pytest.raises(ClaimNotFoundError):
        await service.confirm_claim(uuid.uuid4(), uuid.uuid4(), confirmed_by=None)


async def test_confirm_claim_from_another_tenant_raises(service: BusinessBlueprintService) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_a, created_by=None)
    claim = await service.propose_claim(
        tenant_a, blueprint.id, section_key=BlueprintSectionKey.GOALS.value,
        claim_type=ClaimType.FACT.value, key="goals.primary", value="x",
        confidence=None, provenance=ClaimProvenance.USER_STATED.value,
        discovery_turn_id=None, evidence_ref=None,
    )
    with pytest.raises(ClaimNotFoundError):
        await service.confirm_claim(tenant_b, claim.id, confirmed_by=None)


async def _confirm_minimum_bar(service: BusinessBlueprintService, tenant_id: uuid.UUID, blueprint_id: uuid.UUID) -> None:
    from app.models.business_blueprint import MINIMUM_BAR_SECTIONS

    for key in MINIMUM_BAR_SECTIONS:
        claim = await service.propose_claim(
            tenant_id, blueprint_id, section_key=key.value, claim_type=ClaimType.FACT.value,
            key=f"{key.value.lower()}.value", value="filled in",
            confidence=None, provenance=ClaimProvenance.USER_STATED.value,
            discovery_turn_id=None, evidence_ref=None,
        )
        await service.confirm_claim(tenant_id, claim.id, confirmed_by=None)


async def test_activate_requires_minimum_bar(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    with pytest.raises(BlueprintActivationError):
        await service.activate(tenant_id, blueprint.id, activated_by=None)


async def test_activate_succeeds_once_minimum_bar_complete(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    await _confirm_minimum_bar(service, tenant_id, blueprint.id)

    activated = await service.activate(tenant_id, blueprint.id, activated_by=uuid.uuid4())
    assert activated.status == BlueprintStatus.ACTIVE
    assert activated.confirmed_at is not None

    active = await service.get_active(tenant_id)
    assert active.id == activated.id


async def test_cannot_activate_twice(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    await _confirm_minimum_bar(service, tenant_id, blueprint.id)
    await service.activate(tenant_id, blueprint.id, activated_by=None)
    with pytest.raises(BlueprintActivationError):
        await service.activate(tenant_id, blueprint.id, activated_by=None)


async def test_only_one_active_blueprint_per_tenant(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    await _confirm_minimum_bar(service, tenant_id, blueprint.id)
    await service.activate(tenant_id, blueprint.id, activated_by=None)

    # get_or_create_draft now returns the ACTIVE blueprint (no second DRAFT
    # created while one is already ACTIVE and un-superseded).
    same = await service.get_or_create_draft(tenant_id, created_by=None)
    assert same.id == blueprint.id


async def test_update_section_on_active_blueprint_creates_new_version(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    await _confirm_minimum_bar(service, tenant_id, blueprint.id)
    activated = await service.activate(tenant_id, blueprint.id, activated_by=None)
    assert activated.version == 1

    new_blueprint, section = await service.update_section(
        tenant_id, activated.id, section_key=BlueprintSectionKey.MARKETING.value,
        data={"channels": ["seo", "referral"]}, updated_by=uuid.uuid4(),
    )
    assert new_blueprint.version == 2
    assert new_blueprint.status == BlueprintStatus.ACTIVE
    assert section.data == {"channels": ["seo", "referral"]}

    old = await service.get_version(tenant_id, 1)
    assert old.status == BlueprintStatus.SUPERSEDED

    # Old version's sections are untouched — "what did we believe at time T".
    old_sections = await service.list_sections(tenant_id, old.id)
    old_marketing = next(s for s in old_sections if s.section_key == BlueprintSectionKey.MARKETING.value)
    assert old_marketing.data != {"channels": ["seo", "referral"]}

    # Other (unedited) sections carried forward into the new version.
    new_sections = await service.list_sections(tenant_id, new_blueprint.id)
    new_identity = next(s for s in new_sections if s.section_key == BlueprintSectionKey.IDENTITY.value)
    old_identity = next(s for s in old_sections if s.section_key == BlueprintSectionKey.IDENTITY.value)
    assert new_identity.data == old_identity.data

    only_one_active = await service.get_active(tenant_id)
    assert only_one_active.id == new_blueprint.id


async def test_cannot_edit_a_superseded_blueprint(service: BusinessBlueprintService) -> None:
    tenant_id = uuid.uuid4()
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    await _confirm_minimum_bar(service, tenant_id, blueprint.id)
    activated = await service.activate(tenant_id, blueprint.id, activated_by=None)
    await service.update_section(
        tenant_id, activated.id, section_key=BlueprintSectionKey.MARKETING.value, data={"x": 1}, updated_by=None
    )
    with pytest.raises(InvalidClaimTransitionError):
        await service.update_section(
            tenant_id, activated.id, section_key=BlueprintSectionKey.MARKETING.value, data={"y": 2}, updated_by=None
        )
