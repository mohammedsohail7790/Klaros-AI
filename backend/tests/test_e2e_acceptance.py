"""section 13: the Phase 2 end-to-end acceptance scenarios.

Temporal itself isn't exercised here (see tests/test_temporal_workflows.py
and PROJECT_STATUS.md for why) — this suite proves the orchestration
backbone Temporal workflows call into: event -> subscriber -> typed tool
-> authorization -> audit, plus the failure and duplicate paths.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.event import Event, EventType
from app.models.rbac import Permission, Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


async def test_happy_path_event_to_tool_to_audit(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    handled: list[str] = []

    async def on_job_completed(event: Event) -> None:
        context = ExecutionContext(
            tenant_id=event.tenant_id,
            actor_type=ActorType.WORKFLOW,
            actor_id=None,
            role=None,
            correlation_id=event.correlation_id,
        )
        await tool_registry.execute(
            "notifications.create_notification",
            {"title": "Job completed", "body": f"Job {event.entity_id} is done", "category": "operations"},
            context,
        )
        handled.append(str(event.id))

    event_bus.subscribe(EventType.JOB_COMPLETED, "job_completion_notifier", on_job_completed)

    event = await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.JOB_COMPLETED,
        source="test",
        entity_type="job",
        entity_id=uuid.uuid4(),
        payload={},
        correlation_id=correlation_id,
    )

    stats = await event_bus.process_pending(EventType.JOB_COMPLETED)

    # +1 for the default audit_recorder handler that subscribes to every event type.
    assert stats.succeeded == 2
    assert handled == [str(event.id)]

    async with event_bus.session_factory() as session:
        completion_event_row = await session.get(Event, event.id)
        assert completion_event_row.status == "PROCESSED"

        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.correlation_id == correlation_id)
            )
        ).scalars().all()

    tool_audit = [r for r in audit_rows if r.tool == "notifications.create_notification"]
    assert len(tool_audit) == 1
    assert tool_audit[0].result == "success"
    assert tool_audit[0].actor_type == ActorType.WORKFLOW


async def test_failure_path_unauthorized_tool_never_executes(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    side_effects: list[str] = []

    async def on_lead_created(event: Event) -> None:
        # This handler deliberately asks for a role that lacks the required
        # permission, simulating an AI/workflow trying to run a tool it isn't
        # authorized for.
        context = ExecutionContext(
            tenant_id=event.tenant_id,
            actor_type=ActorType.AI,
            actor_id=None,
            role=Role.READ_ONLY,
        )
        await tool_registry.execute(
            "notifications.create_notification",
            {"title": "should not send", "body": "x"},
            context,
        )
        side_effects.append("this must never run")

    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    original_policy = DEFAULT_TOOL_POLICIES.get("notifications.create_notification")
    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.AUTO
    tool_registry.get("notifications.create_notification").required_permission = Permission.MANAGE_USERS

    try:
        event_bus.subscribe(EventType.LEAD_CREATED, "unauthorized_handler", on_lead_created)
        event = await event_bus.publish(
            tenant_id=tenant_id, event_type=EventType.LEAD_CREATED, source="test", payload={}
        )
        stats = await event_bus.process_pending(EventType.LEAD_CREATED)

        assert stats.dead_lettered == 1  # handler exception -> retried -> dead-lettered
        assert side_effects == []  # the notification tool body never ran

        async with event_bus.session_factory() as session:
            failure_audits = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id, AuditLog.result == "failure"
                    )
                )
            ).scalars().all()
        assert any(a.tool == "notifications.create_notification" for a in failure_audits)
    finally:
        tool_registry.get("notifications.create_notification").required_permission = None
        if original_policy is None:
            DEFAULT_TOOL_POLICIES.pop("notifications.create_notification", None)
        else:
            DEFAULT_TOOL_POLICIES["notifications.create_notification"] = original_policy


async def test_duplicate_event_delivered_twice_executes_action_once(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    notification_ids: list[str] = []

    async def create_notification_once(event: Event) -> None:
        context = ExecutionContext(
            tenant_id=event.tenant_id, actor_type=ActorType.SYSTEM, actor_id=None, role=None
        )
        output = await tool_registry.execute(
            "notifications.create_notification",
            {"title": "Invoice created", "body": "x", "category": "finance"},
            context,
        )
        notification_ids.append(output.notification_id)

    event_bus.subscribe(EventType.INVOICE_CREATED, "invoice_notifier", create_notification_once)

    event = await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.INVOICE_CREATED,
        source="billing-webhook",
        payload={"invoice_id": "inv_1"},
        idempotency_key="billing-webhook-delivery-1",
    )

    await event_bus.process_pending(EventType.INVOICE_CREATED)
    assert len(notification_ids) == 1

    # Same webhook redelivers (identical idempotency_key) -> publish() returns
    # the original event row instead of creating a second one or re-queueing it.
    duplicate_event = await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.INVOICE_CREATED,
        source="billing-webhook",
        payload={"invoice_id": "inv_1"},
        idempotency_key="billing-webhook-delivery-1",
    )
    assert duplicate_event.id == event.id

    # And even a raw re-delivery of the same message to the handler (bypassing
    # publish-level dedup entirely) is caught at the handler level:
    result = await event_bus._handle_one(
        event.id, event_bus._subscriptions[EventType.INVOICE_CREATED][0]
    )
    assert result is None
    assert len(notification_ids) == 1  # still exactly one notification created
