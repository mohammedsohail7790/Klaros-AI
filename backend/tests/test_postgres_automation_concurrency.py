"""Real PostgreSQL concurrency verification for the Automation Engine's
idempotency guarantee (Rule 10/23): SQLite serializes writers to the same
file, so it can never prove a genuine race the way real PostgreSQL can
(see tests/test_postgres_appointment_concurrency.py's own header for the
precedent this test follows). `AutomationExecution`'s real DB unique
constraint on (automation_version_id, source_event_id) must survive N
truly concurrent inserts with exactly one winner — a duplicate webhook
delivery, retried event, or concurrently-racing workers must never
produce two executions (and therefore never two duplicate notifications,
appointments, invoices, etc. downstream).
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.automation import AutomationExecution, TriggerType
from app.services.automation_service import AutomationService
from app.tools.factory import build_tool_registry

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _service() -> AutomationService:
    # A minimal, dependency-free EventBus-less ToolRegistry is enough here —
    # these tests exercise start_execution's own idempotency directly and
    # never reach a governed action (steps intentionally use a real allowed
    # no-op-shaped action so validation passes, but the concurrency itself
    # is proven at the AutomationExecution row level, independent of what
    # the action does).
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    return AutomationService(async_session_maker, AIExecutionService(registry))


@requires_real_postgres
async def test_ten_concurrent_duplicate_events_produce_exactly_one_execution() -> None:
    tenant_id = uuid.uuid4()
    service = _service()
    automation = await service.create_automation(
        tenant_id, name="Concurrency Dedup", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": "lead.created"}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    async def fire(label: str):
        return await service.start_execution(
            tenant_id, published, version, trigger_type=TriggerType.EVENT, source_event_id=event_id,
            entity_type="lead", entity_id=entity_id, context={}, triggered_by=None,
        )

    results = await asyncio.gather(*[fire(f"racer-{i}") for i in range(10)])
    successes = [r for r in results if r is not None]
    assert len(successes) == 1, f"expected exactly one non-deduplicated execution, got {len(successes)}"

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AutomationExecution).where(
                    AutomationExecution.automation_version_id == version.id,
                    AutomationExecution.source_event_id == event_id,
                )
            )
        ).scalars().all()
    assert len(rows) == 1  # proven at the database level, not just the Python return values


@requires_real_postgres
async def test_concurrent_distinct_events_all_produce_their_own_execution() -> None:
    """The unique constraint must not over-serialize unrelated events —
    only genuine (version, source_event_id) collisions should dedupe."""
    tenant_id = uuid.uuid4()
    service = _service()
    automation = await service.create_automation(
        tenant_id, name="Concurrency Distinct", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": "lead.created"}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    async def fire(event_id: uuid.UUID):
        return await service.start_execution(
            tenant_id, published, version, trigger_type=TriggerType.EVENT, source_event_id=event_id,
            entity_type="lead", entity_id=uuid.uuid4(), context={}, triggered_by=None,
        )

    event_ids = [uuid.uuid4() for _ in range(8)]
    results = await asyncio.gather(*[fire(eid) for eid in event_ids])
    assert all(r is not None for r in results)
    assert len({r.id for r in results}) == 8
