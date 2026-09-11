"""app/services/company_memory_service.py — the governed Company Memory
CRUD/confirm/reject/revoke/context lifecycle."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import async_session_maker
from app.models.company_memory import MemorySource, MemoryStatus, MemoryType
from app.services.company_memory_service import (
    CompanyMemoryService,
    MemoryAuthorityError,
    MemoryNotFoundError,
    MemoryStateError,
    MemoryValidationError,
)

pytestmark = pytest.mark.asyncio


def _service() -> CompanyMemoryService:
    return CompanyMemoryService(async_session_maker)


# --- Validation ---

async def test_invalid_key_rejected() -> None:
    service = _service()
    with pytest.raises(MemoryValidationError):
        await service.create_memory(
            uuid.uuid4(), memory_type=MemoryType.OWNER_PREFERENCE, key="Not A Valid Key!",
            value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        )


async def test_invalid_type_rejected() -> None:
    service = _service()
    with pytest.raises(MemoryValidationError):
        await service.create_memory(
            uuid.uuid4(), memory_type="NOT_A_TYPE", key="preferred_time",
            value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        )


async def test_empty_value_rejected() -> None:
    service = _service()
    with pytest.raises(MemoryValidationError):
        await service.create_memory(
            uuid.uuid4(), memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_time",
            value="", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        )


async def test_oversized_value_rejected() -> None:
    service = _service()
    with pytest.raises(MemoryValidationError):
        await service.create_memory(
            uuid.uuid4(), memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_time",
            value="x" * 3000, description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        )


async def test_create_memory_rejects_ai_proposed_source() -> None:
    """create_memory is the human-authored entry point only."""
    service = _service()
    with pytest.raises(MemoryValidationError):
        await service.create_memory(
            uuid.uuid4(), memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_time",
            value="morning", description=None, source=MemorySource.AI_PROPOSED, created_by=None,
        )


# --- Create / supersession ---

async def test_create_memory_is_active_immediately() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    memory = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=uuid.uuid4(),
    )
    assert memory.status == MemoryStatus.ACTIVE


async def test_setting_a_new_value_supersedes_the_old_one() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    v1 = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    v2 = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="afternoon", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    assert v2.status == MemoryStatus.ACTIVE
    assert v2.supersedes_id == v1.id

    v1_reloaded = await service.get_memory(tenant_id, v1.id)
    assert v1_reloaded.status == MemoryStatus.ARCHIVED

    active = await service.list_memories(tenant_id, key="preferred_appointment_time", status=MemoryStatus.ACTIVE)
    assert len(active) == 1
    assert active[0].id == v2.id


async def test_history_preserves_full_chain() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="afternoon", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    history = await service.get_history(tenant_id, "preferred_appointment_time")
    assert [h.value for h in history] == ["morning", "afternoon"]
    assert [h.status for h in history] == [MemoryStatus.ARCHIVED, MemoryStatus.ACTIVE]


# --- Authority ---

async def test_lower_authority_cannot_silently_supersede_higher_authority() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    with pytest.raises(MemoryAuthorityError):
        await service.create_memory(
            tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
            value="afternoon", description=None, source=MemorySource.OWNER_APPROVAL, created_by=None,
        )


async def test_equal_or_higher_authority_can_supersede() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_APPROVAL, created_by=None,
    )
    v2 = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="afternoon", description=None, source=MemorySource.OWNER_CORRECTION, created_by=None,
    )
    assert v2.status == MemoryStatus.ACTIVE


# --- AI-proposed / confirm / reject ---

async def test_propose_memory_is_always_pending_and_ai_proposed() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    memory = await service.propose_memory(
        tenant_id, memory_type=MemoryType.OPERATIONAL_PREFERENCE, key="preferred_customer_segment",
        value="commercial", description="Inferred from recent activity",
        source_entity_type="morning_brief_recommendation", source_entity_id=uuid.uuid4(), confidence=0.7,
    )
    assert memory.status == MemoryStatus.PENDING
    assert memory.source == MemorySource.AI_PROPOSED


async def test_pending_memory_never_appears_in_context() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    await service.propose_memory(
        tenant_id, memory_type=MemoryType.OPERATIONAL_PREFERENCE, key="preferred_customer_segment",
        value="commercial", description=None, source_entity_type=None, source_entity_id=None,
    )
    context = await service.get_context(tenant_id)
    assert context == []


async def test_confirm_moves_pending_to_active_and_enters_context() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    proposed = await service.propose_memory(
        tenant_id, memory_type=MemoryType.OPERATIONAL_PREFERENCE, key="preferred_customer_segment",
        value="commercial", description=None, source_entity_type=None, source_entity_id=None,
    )
    confirmed = await service.confirm_memory(tenant_id, proposed.id, confirmed_by=uuid.uuid4())
    assert confirmed.status == MemoryStatus.ACTIVE
    assert confirmed.source == MemorySource.AI_PROPOSED  # source is preserved, only status changes

    context = await service.get_context(tenant_id)
    assert len(context) == 1
    assert context[0].key == "preferred_customer_segment"
    assert context[0].source == MemorySource.AI_PROPOSED


async def test_reject_moves_pending_to_rejected_never_active() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    proposed = await service.propose_memory(
        tenant_id, memory_type=MemoryType.OPERATIONAL_PREFERENCE, key="preferred_customer_segment",
        value="commercial", description=None, source_entity_type=None, source_entity_id=None,
    )
    rejected = await service.reject_memory(tenant_id, proposed.id, rejected_by=uuid.uuid4(), reason="Not accurate")
    assert rejected.status == MemoryStatus.REJECTED
    context = await service.get_context(tenant_id)
    assert context == []


async def test_cannot_confirm_a_non_pending_memory() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    memory = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    with pytest.raises(MemoryStateError):
        await service.confirm_memory(tenant_id, memory.id, confirmed_by=None)


async def test_update_pending_memory_edits_in_place() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    proposed = await service.propose_memory(
        tenant_id, memory_type=MemoryType.OPERATIONAL_PREFERENCE, key="preferred_customer_segment",
        value="commercial", description=None, source_entity_type=None, source_entity_id=None,
    )
    edited = await service.update_pending_memory(tenant_id, proposed.id, value="residential", description="corrected", updated_by=uuid.uuid4())
    assert edited.value == "residential"
    assert edited.status == MemoryStatus.PENDING


async def test_cannot_edit_an_active_memory_in_place() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    memory = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    with pytest.raises(MemoryStateError):
        await service.update_pending_memory(tenant_id, memory.id, value="afternoon", description=None, updated_by=None)


# --- Revoke ---

async def test_revoke_removes_from_context() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    memory = await service.create_memory(
        tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    assert len(await service.get_context(tenant_id)) == 1

    revoked = await service.revoke_memory(tenant_id, memory.id, revoked_by=uuid.uuid4(), reason="No longer applies")
    assert revoked.status == MemoryStatus.REVOKED
    assert await service.get_context(tenant_id) == []


async def test_cannot_revoke_a_non_active_memory() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    proposed = await service.propose_memory(
        tenant_id, memory_type=MemoryType.OPERATIONAL_PREFERENCE, key="preferred_customer_segment",
        value="commercial", description=None, source_entity_type=None, source_entity_id=None,
    )
    with pytest.raises(MemoryStateError):
        await service.revoke_memory(tenant_id, proposed.id, revoked_by=None)


# --- Effective dates ---

async def test_future_effective_from_excluded_from_context() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    future = datetime.now(timezone.utc) + timedelta(days=30)
    await service.create_memory(
        tenant_id, memory_type=MemoryType.TEMPORAL_CONTEXT, key="winter_campaign_active",
        value="true", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        effective_from=future,
    )
    assert await service.get_context(tenant_id) == []


async def test_expired_effective_until_excluded_from_context() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await service.create_memory(
        tenant_id, memory_type=MemoryType.TEMPORAL_CONTEXT, key="summer_campaign_active",
        value="true", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        effective_until=past,
    )
    assert await service.get_context(tenant_id) == []


async def test_currently_effective_memory_included_in_context() -> None:
    service = _service()
    tenant_id = uuid.uuid4()
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    await service.create_memory(
        tenant_id, memory_type=MemoryType.TEMPORAL_CONTEXT, key="active_campaign",
        value="true", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        effective_from=past, effective_until=future,
    )
    context = await service.get_context(tenant_id)
    assert len(context) == 1


# --- Tenant isolation ---

async def test_context_is_tenant_isolated() -> None:
    service = _service()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await service.create_memory(
        tenant_a, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    await service.create_memory(
        tenant_b, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="afternoon", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    context_a = await service.get_context(tenant_a)
    context_b = await service.get_context(tenant_b)
    assert len(context_a) == 1 and context_a[0].value == "morning"
    assert len(context_b) == 1 and context_b[0].value == "afternoon"


async def test_context_is_bounded_even_with_many_active_memories() -> None:
    """Rule 11/24: never dump unbounded memory into an AI prompt."""
    from app.services.company_memory_service import MAX_CONTEXT_ENTRIES

    service = _service()
    tenant_id = uuid.uuid4()
    for i in range(MAX_CONTEXT_ENTRIES + 10):
        await service.create_memory(
            tenant_id, memory_type=MemoryType.OWNER_PREFERENCE, key=f"preference_{i}", value="x",
            description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        )
    context = await service.get_context(tenant_id)
    assert len(context) == MAX_CONTEXT_ENTRIES


async def test_oversized_description_rejected() -> None:
    service = _service()
    with pytest.raises(MemoryValidationError):
        await service.create_memory(
            uuid.uuid4(), memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_time",
            value="morning", description="x" * 3000, source=MemorySource.OWNER_EXPLICIT, created_by=None,
        )


async def test_cannot_read_or_mutate_another_tenants_memory() -> None:
    service = _service()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    memory = await service.create_memory(
        tenant_a, memory_type=MemoryType.OWNER_PREFERENCE, key="preferred_appointment_time",
        value="morning", description=None, source=MemorySource.OWNER_EXPLICIT, created_by=None,
    )
    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(tenant_b, memory.id)
    with pytest.raises(MemoryNotFoundError):
        await service.revoke_memory(tenant_b, memory.id, revoked_by=None)
