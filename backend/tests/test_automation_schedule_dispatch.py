"""AutomationService.check_and_dispatch_scheduled — the SCHEDULE trigger's
dispatcher. Real ToolRegistry actions, real due-detection against a real
Organization.timezone, real duplicate-tick idempotency semantics (the
actual DB-constraint proof lives in
tests/test_postgres_schedule_concurrency.py; this file covers the
service-level behavior that doesn't need real Postgres concurrency)."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.automation import AutomationExecution, AutomationStatus, ExecutionStatus, TriggerType
from app.models.notification import Notification
from app.models.organization import Organization
from app.services.automation_service import AutomationNotFoundError, AutomationService, AutomationValidationError

pytestmark = pytest.mark.asyncio

FRIDAY_10AM_UTC = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)  # past a 09:00 UTC target


def _service(tool_registry) -> AutomationService:
    return AutomationService(async_session_maker, AIExecutionService(tool_registry))


async def _seed_org(timezone_name: str = "UTC") -> uuid.UUID:
    async with async_session_maker() as session:
        org = Organization(name="Schedule Test Co", slug=f"schedule-test-{uuid.uuid4().hex[:8]}", timezone=timezone_name)
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org.id


def _notify_step(title: str = "Scheduled") -> list[dict]:
    return [{"action": "notifications.create_notification", "params": {"title": title, "body": "scheduled run"}}]


async def test_schedule_trigger_requires_valid_config(tool_registry) -> None:
    tenant_id = await _seed_org()
    service = _service(tool_registry)
    with pytest.raises(AutomationValidationError):
        await service.create_automation(
            tenant_id, name="Bad Schedule", description=None, trigger_type=TriggerType.SCHEDULE,
            trigger_config={"frequency": "YEARLY", "time": "09:00"}, condition=None, steps=_notify_step(),
            created_by=None,
        )


async def test_daily_schedule_dispatches_when_due(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Daily Notify", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step("Daily fired"),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert len(dispatched) == 1

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Daily fired"))
        ).scalars().all()
    assert len(notifications) == 1


async def test_daily_schedule_not_due_yet_does_not_dispatch(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Not Due Yet", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    before_target = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=before_target)
    assert dispatched == []


async def test_disabled_automation_never_dispatches(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Never Published", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    # Never published/enabled.
    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert dispatched == []

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert executions == []


async def test_disabling_after_publish_stops_dispatch(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Will Be Disabled", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    await service.set_enabled(tenant_id, automation.id, False)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert dispatched == []


async def test_duplicate_tick_same_day_does_not_double_dispatch(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="No Double Fire", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step("Once only"),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    first_tick = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    later_same_day = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    second_tick = await service.check_and_dispatch_scheduled(tenant_id, now_utc=later_same_day)

    assert len(first_tick) == 1
    assert second_tick == []  # already fired for this tenant-local date

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert len(executions) == 1


async def test_missed_schedule_still_fires_once_when_checked_late(tool_registry) -> None:
    """Rule 8 Policy A: a late tick (scheduler was 'down' past the target
    time) still fires the day's occurrence exactly once."""
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Late Tick", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    very_late = datetime(2026, 9, 4, 23, 30, tzinfo=timezone.utc)
    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=very_late)
    assert len(dispatched) == 1


async def test_next_day_occurrence_fires_again(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Fires Daily", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    day_one = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    next_day = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    day_two = await service.check_and_dispatch_scheduled(tenant_id, now_utc=next_day)

    assert len(day_one) == 1
    assert len(day_two) == 1
    assert day_one[0] != day_two[0]


async def test_weekly_schedule_only_fires_on_matching_weekday(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Mondays Only", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "WEEKLY", "time": "09:00", "weekdays": [0]}, condition=None,
        steps=_notify_step(), created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    friday_dispatch = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert friday_dispatch == []

    monday = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)  # 2026-09-07 is a Monday
    monday_dispatch = await service.check_and_dispatch_scheduled(tenant_id, now_utc=monday)
    assert len(monday_dispatch) == 1


async def test_tenant_timezone_shifts_the_due_time(tool_registry) -> None:
    """09:00 UTC must not fire a 09:00-local schedule for a tenant in a
    timezone where it isn't yet 09:00 local."""
    tenant_id = await _seed_org("America/Los_Angeles")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="LA Schedule", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    nine_am_utc = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)  # 02:00 in Los Angeles
    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=nine_am_utc)
    assert dispatched == []

    five_pm_utc = datetime(2026, 9, 4, 17, 0, tzinfo=timezone.utc)  # 10:00 in Los Angeles
    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=five_pm_utc)
    assert len(dispatched) == 1


async def test_condition_gates_scheduled_dispatch(tool_registry) -> None:
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Conditional Schedule", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"},
        condition={"field": "schedule.occurrence_date", "op": "eq", "value": "1999-01-01"},
        steps=_notify_step("Should not fire"), created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert len(dispatched) == 1  # the execution itself still runs...

    async with async_session_maker() as session:
        execution = await session.get(AutomationExecution, dispatched[0])
        assert execution.status == ExecutionStatus.COMPLETED

        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Should not fire"))
        ).scalars().all()
    assert notifications == []  # ...but the condition blocked the action


async def test_scheduled_dispatch_is_tenant_isolated(tool_registry) -> None:
    tenant_a = await _seed_org("UTC")
    tenant_b = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_a, name="A Only", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step(),
        created_by=None,
    )
    await service.publish(tenant_a, automation.id)

    dispatched = await service.check_and_dispatch_scheduled(tenant_b, now_utc=FRIDAY_10AM_UTC)
    assert dispatched == []


async def test_version_selection_uses_current_published_version(tool_registry) -> None:
    """Rule 10: a republish before a schedule fires should use the new
    version; an execution already created keeps its own version FK."""
    tenant_id = await _seed_org("UTC")
    service = _service(tool_registry)
    automation = await service.create_automation(
        tenant_id, name="Versioned Schedule", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step("v1"),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    v1 = (await service.list_versions(tenant_id, automation.id))[0]

    await service.update_automation(
        tenant_id, automation.id, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None, steps=_notify_step("v2"),
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    v2 = (await service.list_versions(tenant_id, automation.id))[1]

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert len(dispatched) == 1

    async with async_session_maker() as session:
        execution = await session.get(AutomationExecution, dispatched[0])
        assert execution.automation_version_id == v2.id
        assert execution.automation_version_id != v1.id
