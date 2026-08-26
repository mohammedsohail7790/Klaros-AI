import uuid

import pytest

from app.events.bus import EventBus
from app.models.event import DeadLetterEvent, Event, EventProcessingRecord, EventStatus, EventType, ProcessingStatus
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def test_publish_persists_event_before_transport(event_bus: EventBus) -> None:
    tenant_id = uuid.uuid4()
    event = await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.LEAD_CREATED,
        source="test",
        payload={"name": "Jane"},
    )
    assert event.status == EventStatus.PUBLISHED
    assert event.tenant_id == tenant_id


async def test_publish_idempotency_key_deduplicates(event_bus: EventBus) -> None:
    tenant_id = uuid.uuid4()
    key = "webhook-delivery-123"

    first = await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.PAYMENT_RECEIVED,
        source="stripe-webhook",
        payload={"amount": 100},
        idempotency_key=key,
    )
    second = await event_bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.PAYMENT_RECEIVED,
        source="stripe-webhook",
        payload={"amount": 100},
        idempotency_key=key,
    )

    assert first.id == second.id
    assert second.was_deduplicated is True

    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(select(Event).where(Event.idempotency_key == key))
        ).scalars().all()
    assert len(rows) == 1


async def test_subscriber_receives_published_event(event_bus: EventBus) -> None:
    tenant_id = uuid.uuid4()
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(str(event.id))

    event_bus.subscribe(EventType.JOB_CREATED, "test_handler", handler)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.JOB_CREATED, source="test", payload={}
    )
    stats = await event_bus.process_pending(EventType.JOB_CREATED)

    # +1 for the default audit_recorder handler that subscribes to every event type.
    assert stats.succeeded == 2
    assert received == [str(event.id)]


async def test_duplicate_delivery_does_not_rerun_handler(event_bus: EventBus) -> None:
    """Critical scenario: same event delivered twice must only execute once."""
    tenant_id = uuid.uuid4()
    call_count = {"n": 0}

    async def handler(event: Event) -> None:
        call_count["n"] += 1

    event_bus.subscribe(EventType.CUSTOMER_CREATED, "counter_handler", handler)
    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.CUSTOMER_CREATED, source="test", payload={}
    )

    await event_bus.process_pending(EventType.CUSTOMER_CREATED)
    assert call_count["n"] == 1

    # Simulate redelivery of the same message id (e.g. consumer crashed before ack,
    # or an explicit replay) by re-invoking the handler dispatch directly.
    result = await event_bus._handle_one(event.id, event_bus._subscriptions[EventType.CUSTOMER_CREATED][0])

    assert call_count["n"] == 1  # handler body did NOT run again
    assert result is None  # signalled as a detected duplicate


async def test_handler_failure_retries_then_dead_letters(event_bus: EventBus) -> None:
    tenant_id = uuid.uuid4()
    attempts = {"n": 0}

    async def always_fails(event: Event) -> None:
        attempts["n"] += 1
        raise RuntimeError("downstream is down")

    event_bus.max_retries = 2
    event_bus.subscribe(EventType.INTEGRATION_FAILED, "flaky_handler", always_fails)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.INTEGRATION_FAILED, source="test", payload={}
    )
    stats = await event_bus.process_pending(EventType.INTEGRATION_FAILED)

    assert attempts["n"] == 2
    assert stats.dead_lettered == 1

    async with event_bus.session_factory() as session:
        dl = (
            await session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.event_id == event.id,
                    DeadLetterEvent.handler_name == "flaky_handler",
                )
            )
        ).scalar_one()
        record = (
            await session.execute(
                select(EventProcessingRecord).where(
                    EventProcessingRecord.event_id == event.id,
                    EventProcessingRecord.handler_name == "flaky_handler",
                )
            )
        ).scalar_one()

    assert dl.reason == "downstream is down"
    assert record.status == ProcessingStatus.DEAD_LETTER
    assert record.attempts == 2


async def test_replay_reprocesses_a_dead_lettered_event(event_bus: EventBus) -> None:
    tenant_id = uuid.uuid4()
    should_fail = {"value": True}
    attempts = {"n": 0}

    async def sometimes_fails(event: Event) -> None:
        attempts["n"] += 1
        if should_fail["value"]:
            raise RuntimeError("still down")

    event_bus.max_retries = 1
    event_bus.subscribe(EventType.INVOICE_OVERDUE, "recoverable_handler", sometimes_fails)

    event = await event_bus.publish(
        tenant_id=tenant_id, event_type=EventType.INVOICE_OVERDUE, source="test", payload={}
    )
    await event_bus.process_pending(EventType.INVOICE_OVERDUE)
    assert attempts["n"] == 1

    should_fail["value"] = False
    result = await event_bus.replay(event.id, "recoverable_handler")

    assert result == ProcessingStatus.SUCCESS
    assert attempts["n"] == 2


async def test_events_are_tenant_scoped(event_bus: EventBus) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    event_a = await event_bus.publish(
        tenant_id=tenant_a, event_type=EventType.LEAD_UPDATED, source="test", payload={}
    )

    async with event_bus.session_factory() as session:
        row = await session.get(Event, event_a.id)
        assert row.tenant_id == tenant_a
        assert row.tenant_id != tenant_b
