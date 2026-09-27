"""Phase 6 (Agent Runtime Reliability): `AgentTriggerService` — wires
`AgentTriggerSource.SCHEDULED` through `AgentVersion.triggers["schedule"]`,
reusing `automation_schedule.check_due` directly. Functional tests
(SQLite); the mandatory real-Postgres duplicate-dispatch race lives in
test_postgres_agent_trigger_concurrency.py.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.models.agent import AgentAutonomyTier, AgentExecutionStatus, AgentStatus, AgentTriggerSource
from app.models.organization import Organization
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_service import AgentService, InvalidTriggerConfigError
from app.services.agent_trigger_service import AgentTriggerService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        item = self._responses.pop(0) if self._responses else {"action": "COMPLETE", "final_response": "done", "reasoning_summary": "r", "arguments": {}}
        text = item if isinstance(item, str) else json.dumps(item)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


def _trigger_service(tool_registry, responses=()):
    from app.db.session import async_session_maker

    exec_service = AgentExecutionService(async_session_maker, tool_registry)
    rs = AgentReasoningService(async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider(list(responses)))
    return AgentTriggerService(async_session_maker, rs)


async def _org(tenant_id, *, timezone_name="UTC"):
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name="ScheduleCo", slug=f"sched-{tenant_id.hex[:8]}", timezone=timezone_name))
        await session.commit()


async def _agent_with_schedule(tool_registry, tenant_id, *, schedule: dict, status=AgentStatus.ACTIVE):
    from app.db.session import async_session_maker

    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="ScheduledAgent", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x", triggers={"schedule": schedule}, created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    if status != AgentStatus.ACTIVE:
        if status == AgentStatus.PAUSED:
            agent = await agent_service.pause(tenant_id, agent.id)
        elif status == AgentStatus.ARCHIVED:
            agent = await agent_service.archive(tenant_id, agent.id)
    return agent_service, agent, version


async def test_due_daily_schedule_dispatches_an_execution(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    _, agent, _ = await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "09:00", "goal": "daily check-in"},
    )
    now = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)  # after 09:00 UTC
    svc = _trigger_service(tool_registry)
    dispatched = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert len(dispatched) == 1


async def test_not_yet_due_schedule_dispatches_nothing(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "09:00", "goal": "daily check-in"},
    )
    now = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)  # before 09:00 UTC
    svc = _trigger_service(tool_registry)
    dispatched = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert dispatched == []


async def test_disabled_schedule_produces_zero_executions(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": False, "frequency": "DAILY", "time": "00:00", "goal": "never"},
    )
    now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    svc = _trigger_service(tool_registry)
    dispatched = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert dispatched == []


async def test_paused_agent_due_schedule_is_skipped_not_executed(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    from app.db.session import async_session_maker
    from app.models.audit_log import AuditLog
    from sqlalchemy import select

    _, agent, _ = await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "00:00", "goal": "should not run"},
        status=AgentStatus.PAUSED,
    )
    now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    svc = _trigger_service(tool_registry)
    dispatched = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert dispatched == []

    async with async_session_maker() as session:
        logs = (await session.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == "agent.trigger.scheduled.skipped")
        )).scalars().all()
        assert len(logs) == 1
        assert "PAUSED" in logs[0].input_summary["reason"]


async def test_archived_agent_due_schedule_is_skipped(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "00:00", "goal": "should not run"},
        status=AgentStatus.ARCHIVED,
    )
    now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    svc = _trigger_service(tool_registry)
    dispatched = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert dispatched == []


async def test_duplicate_tick_same_occurrence_is_deduplicated(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "09:00", "goal": "daily check-in"},
    )
    now = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
    svc = _trigger_service(tool_registry)
    first = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert len(first) == 1
    # A second tick later the same tenant-local day — even at a different
    # minute — must NOT create a second execution (Rule 8: fire at most
    # once per tenant-local calendar date).
    later_same_day = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    second = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=later_same_day)
    assert second == []

    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(select(AgentExecution).where(AgentExecution.tenant_id == tenant_id))).scalars().all()
        assert len(rows) == 1


async def test_missed_schedule_does_not_catch_up_multiple_days(tool_registry):
    """Missed-schedule policy is skip-never-catch-up: even if `now_utc` is
    far past the target time (simulating "the worker was down for days"),
    exactly one occurrence fires for the CURRENT calendar date — there is
    no code path that walks backward and fires several past-due days."""
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "00:00", "goal": "daily"},
    )
    now = datetime(2026, 1, 10, 23, 0, tzinfo=timezone.utc)  # days past any prior occurrence
    svc = _trigger_service(tool_registry)
    dispatched = await svc.check_and_dispatch_scheduled(tenant_id, now_utc=now)
    assert len(dispatched) == 1  # exactly one, not five/ten


async def test_weekly_schedule_respects_weekday(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id)
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "WEEKLY", "time": "09:00", "weekdays": [0], "goal": "monday only"},  # Monday
    )
    svc = _trigger_service(tool_registry)
    tuesday = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)  # 2026-01-06 is a Tuesday
    assert await svc.check_and_dispatch_scheduled(tenant_id, now_utc=tuesday) == []
    monday = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)  # 2026-01-05 is a Monday
    assert len(await svc.check_and_dispatch_scheduled(tenant_id, now_utc=monday)) == 1


async def test_non_utc_timezone_is_respected(tool_registry):
    tenant_id = uuid.uuid4()
    await _org(tenant_id, timezone_name="America/New_York")  # UTC-5 in January
    await _agent_with_schedule(
        tool_registry, tenant_id,
        schedule={"enabled": True, "frequency": "DAILY", "time": "09:00", "goal": "ny morning"},
    )
    svc = _trigger_service(tool_registry)
    # 13:30 UTC == 08:30 America/New_York in January (EST, UTC-5) — not yet due.
    not_yet = datetime(2026, 1, 5, 13, 30, tzinfo=timezone.utc)
    assert await svc.check_and_dispatch_scheduled(tenant_id, now_utc=not_yet) == []
    # 14:30 UTC == 09:30 America/New_York — due.
    due = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    assert len(await svc.check_and_dispatch_scheduled(tenant_id, now_utc=due)) == 1


# --------------------------------------------------- trigger config validation

async def test_invalid_schedule_frequency_rejected_at_version_creation(tool_registry):
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="BadSchedule", purpose="x", autonomy_tier=AgentAutonomyTier.OBSERVE,
        acting_role=Role.MANAGER, created_by=None,
    )
    with pytest.raises(InvalidTriggerConfigError):
        await agent_service.create_version(
            tenant_id, agent.id, instructions="x",
            triggers={"schedule": {"enabled": True, "frequency": "MONTHLY", "time": "09:00", "goal": "g"}},
            created_by=None,
        )


async def test_invalid_event_type_rejected_at_version_creation(tool_registry):
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="BadEvent", purpose="x", autonomy_tier=AgentAutonomyTier.OBSERVE,
        acting_role=Role.MANAGER, created_by=None,
    )
    with pytest.raises(InvalidTriggerConfigError):
        await agent_service.create_version(
            tenant_id, agent.id, instructions="x",
            triggers={"event": {"enabled": True, "event_type": "not.a.real.event", "goal": "g"}},
            created_by=None,
        )


async def test_valid_schedule_and_event_triggers_accepted(tool_registry):
    from app.db.session import async_session_maker
    from app.models.event import EventType

    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="GoodTriggers", purpose="x", autonomy_tier=AgentAutonomyTier.OBSERVE,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x",
        triggers={
            "schedule": {"enabled": True, "frequency": "DAILY", "time": "09:00", "goal": "g"},
            "event": {"enabled": True, "event_type": EventType.LEAD_CREATED.value, "goal": "g2"},
        },
        created_by=None,
    )
    assert version.triggers["schedule"]["frequency"] == "DAILY"
