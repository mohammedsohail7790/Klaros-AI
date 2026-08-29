"""Phase 12 production hardening: closes the dual-write gap found in the
Phase 11 audit — `EventBus.publish()` commits the `Event` row to Postgres
before enqueueing to the transport (Redis/InMemory) as a separate step. If
the process crashes, or the transport call itself fails, between those two
steps, the row is durable but nothing was ever enqueued to deliver it —
`process_pending` only reads from the transport, never scans this table.
`EventBus.reconcile_stuck_events()` is the outbox-relay pass that recovers
from exactly that state.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.event import Event, EventProcessingRecord, EventStatus, EventType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


async def _insert_orphaned_event(event_bus, tenant_id: uuid.UUID, event_type: str, age_seconds: float) -> uuid.UUID:
    """Simulates a crash between the DB commit and the transport enqueue in
    `publish()` — a real, durable `Event` row that was never sent to the
    transport, which is exactly the state a mid-publish crash leaves
    behind. Bypasses `publish()` deliberately, since calling it would also
    perform the enqueue we're testing the recovery from."""
    async with event_bus.session_factory() as session:
        event = Event(
            tenant_id=tenant_id,
            event_type=event_type,
            source="test",
            payload={},
            correlation_id=uuid.uuid4(),
            status=EventStatus.PUBLISHED,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        event.created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        await session.commit()
        return event.id


async def test_reconcile_enqueues_a_stuck_event_and_it_gets_processed(event_bus, tool_registry) -> None:
    del tool_registry  # ensures handlers (including the notification ones) are registered on event_bus
    tenant_id = uuid.uuid4()
    event_id = await _insert_orphaned_event(event_bus, tenant_id, EventType.LEAD_CREATED, age_seconds=60)

    reconciled_count = await event_bus.reconcile_stuck_events(grace_seconds=30)
    assert reconciled_count >= 1

    # Now that it's been re-enqueued, the normal poll loop picks it up like
    # any other event — no special-cased recovery path in the handler layer.
    await event_bus.process_pending(EventType.LEAD_CREATED)

    async with event_bus.session_factory() as session:
        records = (
            await session.execute(
                select(EventProcessingRecord).where(EventProcessingRecord.event_id == event_id)
            )
        ).scalars().all()
    assert len(records) >= 1


async def test_reconcile_ignores_events_still_within_the_grace_period(event_bus, tool_registry) -> None:
    del tool_registry
    tenant_id = uuid.uuid4()
    await _insert_orphaned_event(event_bus, tenant_id, EventType.LEAD_CREATED, age_seconds=1)

    reconciled_count = await event_bus.reconcile_stuck_events(grace_seconds=30)
    assert reconciled_count == 0


async def test_reconcile_does_not_touch_events_already_processed(event_bus, tool_registry) -> None:
    """A normal, successfully-delivered event (real publish -> real
    process_pending) must never be redundantly re-enqueued once it's
    already PROCESSED."""
    tenant_id = uuid.uuid4()
    ctx = ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
    await tool_registry.execute(
        "crm.create_lead", {"name": "Real Lead", "source": "REFERRAL", "service_requested": "x"}, ctx
    )
    await event_bus.process_pending(EventType.LEAD_CREATED)

    # Force the now-PROCESSED event old enough to be grace-period-eligible,
    # and confirm reconciliation correctly finds zero PUBLISHED rows to
    # touch (its status is PROCESSED, not PUBLISHED).
    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == EventType.LEAD_CREATED))
        ).scalars().all()
        for row in rows:
            row.created_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        await session.commit()

    reconciled_count = await event_bus.reconcile_stuck_events(grace_seconds=30)
    assert reconciled_count == 0
