"""app/events/automation_handlers.py — the EVENT-trigger dispatcher.
Proves a real `lead.created` event, delivered through the real EventBus
(`event_bus.process_pending`, exactly like every other domain handler's
own tests), starts a real, tenant-scoped, idempotent AutomationExecution
and runs its governed action — no second event transport, no fabricated
event type.
"""

import uuid

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.automation import AutomationExecution, ExecutionStatus, TriggerType
from app.models.event import EventType
from app.models.notification import Notification
from app.models.rbac import Role
from app.services.automation_service import AutomationService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


def _service(tool_registry) -> AutomationService:
    return AutomationService(async_session_maker, AIExecutionService(tool_registry))


async def _create_lead(tool_registry, tenant_id) -> str:
    output = await tool_registry.execute(
        "crm.create_lead",
        {
            "name": "Automation Dispatch Lead", "source": "REFERRAL", "email": "dispatch@example.com",
            "service_requested": "Gutter cleaning", "location": "Austin, TX", "urgency": "LOW",
            "estimated_value": 500,
        },
        _ctx(tenant_id),
    )
    return output.lead["id"]


async def test_event_trigger_starts_a_real_execution_on_real_dispatch(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="New Lead Notify", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.LEAD_CREATED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "{{lead.name}}", "body": "follow up"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    await _create_lead(tool_registry, tenant_id)
    stats = await event_bus.process_pending(EventType.LEAD_CREATED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert len(executions) == 1
    assert executions[0].status == ExecutionStatus.COMPLETED
    assert executions[0].trigger_type == TriggerType.EVENT

    async with async_session_maker() as session:
        notifications = (
            await session.execute(
                select(Notification).where(
                    Notification.tenant_id == tenant_id,
                    Notification.title == "Automation Dispatch Lead",
                )
            )
        ).scalars().all()
    assert len(notifications) == 1  # this automation's own notification; the CRM's own lead-created notification is separate


async def test_disabled_automation_does_not_dispatch_on_event(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Draft Only", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.LEAD_CREATED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    # Never published/enabled.

    await _create_lead(tool_registry, tenant_id)
    await event_bus.process_pending(EventType.LEAD_CREATED)

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert executions == []


async def test_event_dispatch_is_tenant_isolated(event_bus, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_a, name="A-only Trigger", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.LEAD_CREATED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    await service.publish(tenant_a, automation.id)

    await _create_lead(tool_registry, tenant_b)  # a lead created for tenant B
    await event_bus.process_pending(EventType.LEAD_CREATED)

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert executions == []  # tenant A's automation never fired for tenant B's event


async def test_condition_on_event_context_gates_dispatch(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="High Value Lead", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.LEAD_CREATED},
        condition={"field": "lead.score", "op": "gte", "value": 999999},
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    await _create_lead(tool_registry, tenant_id)
    await event_bus.process_pending(EventType.LEAD_CREATED)

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert len(executions) == 1
    assert executions[0].status == ExecutionStatus.COMPLETED  # completes, but ran no actions

    async with async_session_maker() as session:
        notifications = (
            await session.execute(
                select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "x")
            )
        ).scalars().all()
    assert notifications == []  # the automation's own step never ran; the CRM's own lead-created notification is separate
