"""app/services/automation_service.py — CRUD, versioning, publish
immutability, manual trigger, governed action execution, the tool
allowlist boundary, and idempotency. Uses the real `tool_registry`/
`event_bus` fixtures (real ToolRegistry -> AIExecutionService -> real
governed tools), matching every other domain-service test in this suite."""

import uuid

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.automation import AutomationExecution, AutomationStatus, ExecutionStatus, StepStatus, TriggerType
from app.models.notification import Notification
from app.services.automation_service import (
    ACTION_ALLOWLIST,
    AutomationNotFoundError,
    AutomationNotPublishedError,
    AutomationService,
    AutomationValidationError,
)

pytestmark = pytest.mark.asyncio


def _service(tool_registry) -> AutomationService:
    return AutomationService(async_session_maker, AIExecutionService(tool_registry))


def _notify_step(title: str = "Hello") -> list[dict]:
    return [{"action": "notifications.create_notification", "params": {"title": title, "body": "test body"}}]


# --- CRUD / versioning ---

async def test_create_automation_creates_version_one(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Test Automation", description=None, trigger_type=TriggerType.MANUAL,
        trigger_config={}, condition=None, steps=_notify_step(), created_by=None,
    )
    assert automation.status == AutomationStatus.DRAFT
    versions = await service.list_versions(tenant_id, automation.id)
    assert len(versions) == 1
    assert versions[0].version_number == 1


async def test_update_creates_a_new_version_never_mutates_existing(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="A", description=None, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step("v1"), created_by=None,
    )
    v2 = await service.update_automation(
        tenant_id, automation.id, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step("v2"), created_by=None,
    )
    assert v2.version_number == 2
    versions = await service.list_versions(tenant_id, automation.id)
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[0].steps[0]["params"]["title"] == "v1"  # untouched


async def test_publish_sets_published_version_and_enables(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="A", description=None, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step(), created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    assert published.status == AutomationStatus.ENABLED
    assert published.published_version_id is not None


async def test_execution_uses_the_version_active_at_start_time_not_a_later_edit(tool_registry) -> None:
    """The versioning guarantee Rule 3 requires: an in-flight execution
    keeps running against the version it started with even if the owner
    edits+republishes the automation afterward."""
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="A", description=None, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step("original"), created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    original_version_id = automation.published_version_id if automation.published_version_id else (await service.get_automation(tenant_id, automation.id)).published_version_id

    execution = await service.trigger_manual(tenant_id, automation.id, context={}, triggered_by=None)
    assert execution.automation_version_id == original_version_id

    # Now edit + republish — a NEW version becomes current.
    await service.update_automation(
        tenant_id, automation.id, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step("edited"), created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    reloaded = await service.get_automation(tenant_id, automation.id)
    assert reloaded.published_version_id != original_version_id

    # The earlier execution's own version FK is untouched.
    old_execution = await service.get_execution(tenant_id, execution.id)
    assert old_execution.automation_version_id == original_version_id


async def test_set_enabled_requires_a_published_version(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="A", description=None, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step(), created_by=None,
    )
    with pytest.raises(AutomationNotPublishedError):
        await service.set_enabled(tenant_id, automation.id, True)


# --- Validation / safety ---

async def test_forbidden_action_is_rejected_at_save_time(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    with pytest.raises(AutomationValidationError):
        await service.create_automation(
            tenant_id, name="Evil", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
            condition=None, steps=[{"action": "crm.delete_customer", "params": {}}], created_by=None,
        )


async def test_unknown_tool_name_is_rejected_at_save_time(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    with pytest.raises(AutomationValidationError):
        await service.create_automation(
            tenant_id, name="Bad", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
            condition=None, steps=[{"action": "totally.unknown.tool", "params": {}}], created_by=None,
        )


async def test_invalid_condition_is_rejected_at_save_time(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    with pytest.raises(AutomationValidationError):
        await service.create_automation(
            tenant_id, name="Bad Condition", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
            condition={"eval": "1+1"}, steps=_notify_step(), created_by=None,
        )


async def test_event_trigger_requires_event_type(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    with pytest.raises(AutomationValidationError):
        await service.create_automation(
            tenant_id, name="Bad Trigger", description=None, trigger_type=TriggerType.EVENT, trigger_config={},
            condition=None, steps=_notify_step(), created_by=None,
        )


async def test_action_allowlist_is_exactly_the_documented_set() -> None:
    assert ACTION_ALLOWLIST == {
        "notifications.create_notification", "crm.update_lead", "crm.create_note",
        "quotes.detect_expired_quotes", "finance.detect_overdue_invoices",
        # Phase 18: the AI Next Action decision layer's own governed entry
        # point — see app/services/ai_next_action_service.py.
        "ai.propose_quote_followup",
        # Phase 20: the second AI Next Action scenario.
        "ai.propose_invoice_followup",
        # Phase 24: the contract-pending-follow-up sweep.
        "contracts.detect_pending",
    }


# --- Execution ---

async def test_manual_trigger_runs_a_real_governed_action(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Notify", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition=None, steps=_notify_step("Manual trigger fired"), created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    execution = await service.trigger_manual(tenant_id, automation.id, context={}, triggered_by=None)
    assert execution.status == ExecutionStatus.COMPLETED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].title == "Manual trigger fired"

    steps = await service.list_execution_steps(tenant_id, execution.id)
    assert len(steps) == 1
    assert steps[0].status == StepStatus.SUCCEEDED


async def test_condition_not_met_completes_without_running_actions(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Conditional", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition={"field": "lead.score", "op": "gte", "value": 999}, steps=_notify_step(), created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    execution = await service.trigger_manual(tenant_id, automation.id, context={"lead": {"score": 10}}, triggered_by=None)
    assert execution.status == ExecutionStatus.COMPLETED
    steps = await service.list_execution_steps(tenant_id, execution.id)
    assert steps == []

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
    assert notifications == []


async def test_condition_met_runs_actions(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Conditional", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition={"field": "lead.score", "op": "gte", "value": 50}, steps=_notify_step(), created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    execution = await service.trigger_manual(tenant_id, automation.id, context={"lead": {"score": 90}}, triggered_by=None)
    assert execution.status == ExecutionStatus.COMPLETED
    steps = await service.list_execution_steps(tenant_id, execution.id)
    assert len(steps) == 1


async def test_disabled_automation_cannot_be_manually_triggered(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Off", description=None, trigger_type=TriggerType.MANUAL, trigger_config={}, condition=None,
        steps=_notify_step(), created_by=None,
    )
    with pytest.raises(AutomationNotPublishedError):
        await service.trigger_manual(tenant_id, automation.id, context={}, triggered_by=None)


async def test_template_param_substitution_uses_real_context_value(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Templated", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "{{lead.name}}", "body": "follow up"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    await service.trigger_manual(tenant_id, automation.id, context={"lead": {"name": "Jane Doe"}}, triggered_by=None)

    async with async_session_maker() as session:
        notification = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().first()
    assert notification.title == "Jane Doe"


# --- Idempotency ---

async def test_duplicate_source_event_id_never_creates_a_second_execution(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Dedup", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": "lead.created"}, condition=None, steps=_notify_step(), created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    event_id = uuid.uuid4()
    first = await service.start_execution(
        tenant_id, published, version, trigger_type=TriggerType.EVENT, source_event_id=event_id,
        entity_type="lead", entity_id=uuid.uuid4(), context={}, triggered_by=None,
    )
    second = await service.start_execution(
        tenant_id, published, version, trigger_type=TriggerType.EVENT, source_event_id=event_id,
        entity_type="lead", entity_id=uuid.uuid4(), context={}, triggered_by=None,
    )
    assert first is not None
    assert second is None  # deduplicated

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.tenant_id == tenant_id))
        ).scalars().all()
    assert len(executions) == 1


# --- Tenant isolation ---

async def test_automations_are_tenant_isolated(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_a, name="A-only", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition=None, steps=_notify_step(), created_by=None,
    )
    assert await service.list_automations(tenant_b) == []
    with pytest.raises(AutomationNotFoundError):
        await service.get_automation(tenant_b, automation.id)
