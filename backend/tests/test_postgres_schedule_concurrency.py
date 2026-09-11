"""Real PostgreSQL concurrency verification for the Automation Engine's
SCHEDULE dispatcher (Rule 7/17/26): two genuinely concurrent scheduler
ticks (simulating overlapping worker processes, or a retried tick) for the
exact same due occurrence must never produce more than one
AutomationExecution — proven against real Postgres, not SQLite, which
would mask the race the same way it masked the appointment double-booking
race in tests/test_postgres_appointment_concurrency.py.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.automation import AutomationExecution, TriggerType
from app.models.organization import Organization
from app.services.automation_service import AutomationService
from app.tools.factory import build_tool_registry

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

FRIDAY_10AM_UTC = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


def _service() -> AutomationService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    return AutomationService(async_session_maker, AIExecutionService(registry))


async def _seed_org() -> uuid.UUID:
    async with async_session_maker() as session:
        org = Organization(name="Schedule Concurrency Co", slug=f"schedule-concurrency-{uuid.uuid4().hex[:8]}", timezone="UTC")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org.id


@requires_real_postgres
async def test_ten_concurrent_scheduler_ticks_produce_exactly_one_execution() -> None:
    tenant_id = await _seed_org()
    service = _service()
    automation = await service.create_automation(
        tenant_id, name="Concurrent Schedule", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    async def tick():
        return await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)

    results = await asyncio.gather(*[tick() for _ in range(10)])
    total_dispatched = sum(len(r) for r in results)
    assert total_dispatched == 1, f"expected exactly one dispatched execution across all ticks, got {total_dispatched}"

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AutomationExecution).where(AutomationExecution.automation_id == automation.id)
            )
        ).scalars().all()
    assert len(rows) == 1  # proven at the database level, not just the Python return values


@requires_real_postgres
async def test_concurrent_ticks_for_distinct_automations_all_dispatch() -> None:
    """The idempotency mechanism must not over-serialize unrelated
    automations — only genuine same-occurrence collisions should dedupe."""
    tenant_id = await _seed_org()
    service = _service()
    automations = []
    for i in range(5):
        automation = await service.create_automation(
            tenant_id, name=f"Distinct Schedule {i}", description=None, trigger_type=TriggerType.SCHEDULE,
            trigger_config={"frequency": "DAILY", "time": "09:00"}, condition=None,
            steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
            created_by=None,
        )
        await service.publish(tenant_id, automation.id)
        automations.append(automation)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=FRIDAY_10AM_UTC)
    assert len(dispatched) == 5
