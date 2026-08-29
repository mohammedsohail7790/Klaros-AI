import asyncio
import uuid

import pytest

from app.events.bus import EventBus
from app.events.metrics import EventWorkerMetrics
from app.events.worker import EventWorker
from app.models.event import DeadLetterEvent, Event, EventProcessingRecord, EventStatus, EventType, ProcessingStatus
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def test_worker_tick_processes_pending_events_without_manual_endpoint(event_bus: EventBus) -> None:
    """The core Phase 8 acceptance criterion at unit scope: publish once,
    let the worker's tick() run (not `bus.process_pending` called by a
    test/API handler directly), and see the side effect happen."""
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(str(event.id))

    event_bus.subscribe(EventType.JOB_ASSIGNED, "worker_test_handler", handler)
    event = await event_bus.publish(
        tenant_id=uuid.uuid4(), event_type=EventType.JOB_ASSIGNED, source="test", payload={}
    )

    worker = EventWorker(event_bus, metrics=EventWorkerMetrics())
    await worker.tick()

    assert received == [str(event.id)]


async def test_worker_run_forever_processes_events_on_its_own_loop(event_bus: EventBus) -> None:
    """A real background asyncio task, not a manual call — publish an event
    *after* the loop is already running, and confirm it gets picked up on a
    later tick without any test code calling process_pending."""
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(str(event.id))

    event_bus.subscribe(EventType.LEAD_ASSIGNED, "run_forever_handler", handler)

    worker = EventWorker(event_bus, poll_interval_seconds=0.02, metrics=EventWorkerMetrics())
    shutdown = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(shutdown))

    await asyncio.sleep(0.03)  # let the loop start and do an empty first tick
    event = await event_bus.publish(
        tenant_id=uuid.uuid4(), event_type=EventType.LEAD_ASSIGNED, source="test", payload={}
    )

    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.02)

    shutdown.set()
    await task

    assert received == [str(event.id)]
    assert worker.metrics.ticks > 0


async def test_worker_graceful_shutdown_stops_the_loop(event_bus: EventBus) -> None:
    worker = EventWorker(event_bus, poll_interval_seconds=0.02, metrics=EventWorkerMetrics())
    shutdown = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(shutdown))
    await asyncio.sleep(0.05)
    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done()


async def test_worker_duplicate_delivery_is_idempotent(event_bus: EventBus) -> None:
    call_count = {"n": 0}

    async def handler(event: Event) -> None:
        call_count["n"] += 1

    event_bus.subscribe(EventType.CUSTOMER_UPDATED, "idempotent_handler", handler)
    await event_bus.publish(tenant_id=uuid.uuid4(), event_type=EventType.CUSTOMER_UPDATED, source="test", payload={})

    worker = EventWorker(event_bus, metrics=EventWorkerMetrics())
    await worker.tick()
    await worker.tick()  # second tick: nothing new pending, but re-run is still safe
    assert call_count["n"] == 1


async def test_worker_handler_failure_retries_then_dead_letters(event_bus: EventBus) -> None:
    attempts = {"n": 0}

    async def always_fails(event: Event) -> None:
        attempts["n"] += 1
        raise RuntimeError("simulated downstream outage")

    event_bus.max_retries = 2
    event_bus.subscribe(EventType.APPOINTMENT_CANCELLED, "flaky_worker_handler", always_fails)
    event = await event_bus.publish(
        tenant_id=uuid.uuid4(), event_type=EventType.APPOINTMENT_CANCELLED, source="test", payload={}
    )

    worker = EventWorker(event_bus, metrics=EventWorkerMetrics())
    await worker.tick()

    assert attempts["n"] == 2
    assert worker.metrics.events_dead_lettered == 1

    async with event_bus.session_factory() as session:
        dl = (
            await session.execute(
                select(DeadLetterEvent).where(
                    DeadLetterEvent.event_id == event.id, DeadLetterEvent.handler_name == "flaky_worker_handler"
                )
            )
        ).scalar_one()
    assert dl.reason == "simulated downstream outage"


async def test_worker_dead_letter_can_be_replayed_and_becomes_processed(event_bus: EventBus) -> None:
    should_fail = {"value": True}
    attempts = {"n": 0}

    async def sometimes_fails(event: Event) -> None:
        attempts["n"] += 1
        if should_fail["value"]:
            raise RuntimeError("still down")

    event_bus.max_retries = 1
    event_bus.subscribe(EventType.REFUND_REQUESTED, "recoverable_worker_handler", sometimes_fails)
    event = await event_bus.publish(
        tenant_id=uuid.uuid4(), event_type=EventType.REFUND_REQUESTED, source="test", payload={}
    )

    worker = EventWorker(event_bus, metrics=EventWorkerMetrics())
    await worker.tick()
    assert attempts["n"] == 1

    should_fail["value"] = False
    result = await event_bus.replay(event.id, "recoverable_worker_handler")
    assert result == ProcessingStatus.SUCCESS

    async with event_bus.session_factory() as session:
        record = (
            await session.execute(
                select(EventProcessingRecord).where(
                    EventProcessingRecord.event_id == event.id,
                    EventProcessingRecord.handler_name == "recoverable_worker_handler",
                )
            )
        ).scalar_one()
        row = await session.get(Event, event.id)
    assert record.status == ProcessingStatus.SUCCESS
    assert record.processed_at is not None
    assert row.status == EventStatus.PROCESSED


async def test_worker_restart_does_not_lose_a_pending_event(event_bus: EventBus) -> None:
    """State lives in Postgres (sqlite here), not in the worker process — a
    fresh EventWorker instance (simulating a restart) picks up an event
    published before it existed."""
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(str(event.id))

    event_bus.subscribe(EventType.JOB_UNASSIGNED, "restart_handler", handler)
    event = await event_bus.publish(
        tenant_id=uuid.uuid4(), event_type=EventType.JOB_UNASSIGNED, source="test", payload={}
    )

    worker_before_restart = EventWorker(event_bus, metrics=EventWorkerMetrics())
    del worker_before_restart  # process "crashes" before ever ticking

    worker_after_restart = EventWorker(event_bus, metrics=EventWorkerMetrics())
    await worker_after_restart.tick()

    assert received == [str(event.id)]


async def test_concurrent_ticks_do_not_duplicate_side_effects(event_bus: EventBus) -> None:
    """Two 'workers' racing to claim the same (event_id, handler_name) pair
    for the first time must not both run the handler.

    This is deliberately NOT exercised via real `asyncio.gather` concurrency
    here: the test suite runs against a single shared in-memory SQLite
    connection (`StaticPool` — see app/db/session.py, a documented sandbox
    substitute for Postgres), and SQLite allows only one active transaction
    per connection — two logically-separate SQLAlchemy sessions sharing that
    one physical connection under real concurrency corrupt each other's
    transaction state (verified: it throws a different, unrelated
    `PendingRollbackError`, not the race this test means to prove). That is
    a limitation of testing multi-connection concurrency against SQLite, not
    of the guarantee itself — in production this is provided by Postgres row
    locking or, as used here, Redis Streams consumer groups (XREADGROUP
    hands a pending message to exactly one consumer), never an in-memory
    lock. What IS testable and real here is the defensive code path added to
    `EventBus._handle_one` for this exact race: if the INSERT of a new
    `EventProcessingRecord` hits the (event_id, handler_name) unique
    constraint — because another writer won the race — the loser must back
    off without running the handler, rather than crash or double-run it.
    """
    call_count = {"n": 0}

    async def handler(event: Event) -> None:
        call_count["n"] += 1

    event_bus.subscribe(EventType.SCOPE_CHANGE_DETECTED, "concurrency_handler", handler)
    event = await event_bus.publish(
        tenant_id=uuid.uuid4(), event_type=EventType.SCOPE_CHANGE_DETECTED, source="test", payload={}
    )
    sub = next(s for s in event_bus._subscriptions[EventType.SCOPE_CHANGE_DETECTED] if s.handler_name == "concurrency_handler")

    # Simulate "worker B already won the race": a SUCCESS record for this
    # exact (event_id, handler_name) pair already committed, without the
    # handler having gone through this process at all.
    async with event_bus.session_factory() as session:
        session.add(
            EventProcessingRecord(
                tenant_id=event.tenant_id,
                event_id=event.id,
                handler_name="concurrency_handler",
                status=ProcessingStatus.SUCCESS,
                attempts=1,
            )
        )
        await session.commit()

    # "Worker A" now processes the same event, unaware worker B already won.
    result = await event_bus._handle_one(event.id, sub)

    assert call_count["n"] == 0  # this process's handler never ran
    assert result is None  # detected as an already-succeeded duplicate, not an error


async def test_concurrent_insert_race_backs_off_instead_of_crashing(event_bus: EventBus, monkeypatch) -> None:
    """Directly exercises the new IntegrityError-catch branch in
    `EventBus._handle_one` (the actual race-safety code, not just its
    outcome): the first attempt to persist a brand-new EventProcessingRecord
    hits a simulated unique-constraint violation (another writer won),
    and the handler must never run."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    call_count = {"n": 0}

    async def handler(event: Event) -> None:
        call_count["n"] += 1

    event_bus.subscribe(EventType.JOB_BLOCKED, "race_handler", handler)
    event = await event_bus.publish(tenant_id=uuid.uuid4(), event_type=EventType.JOB_BLOCKED, source="test", payload={})
    sub = next(s for s in event_bus._subscriptions[EventType.JOB_BLOCKED] if s.handler_name == "race_handler")

    original_commit = AsyncSession.commit
    call_state = {"n": 0}

    async def flaky_commit(self):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise IntegrityError("insert", {}, Exception("UNIQUE constraint failed"))
        return await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    result = await event_bus._handle_one(event.id, sub)

    assert result is None
    assert call_count["n"] == 0


async def test_tenant_isolation_worker_never_leaks_across_tenants(event_bus: EventBus) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    seen_tenants: list[uuid.UUID] = []

    async def handler(event: Event) -> None:
        seen_tenants.append(event.tenant_id)

    event_bus.subscribe(EventType.CREDIT_NOTE_CREATED, "tenant_isolation_handler", handler)
    await event_bus.publish(tenant_id=tenant_a, event_type=EventType.CREDIT_NOTE_CREATED, source="test", payload={})
    await event_bus.publish(tenant_id=tenant_b, event_type=EventType.CREDIT_NOTE_CREATED, source="test", payload={})

    worker = EventWorker(event_bus, metrics=EventWorkerMetrics())
    await worker.tick()

    assert sorted(seen_tenants, key=str) == sorted([tenant_a, tenant_b], key=str)
    assert tenant_a in seen_tenants and tenant_b in seen_tenants
    # each event's own row still carries only its own tenant — no cross-write
    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(select(Event).where(Event.event_type == EventType.CREDIT_NOTE_CREATED))
        ).scalars().all()
    for row in rows:
        assert row.tenant_id in (tenant_a, tenant_b)


async def test_worker_records_metrics(event_bus: EventBus) -> None:
    async def handler(event: Event) -> None:
        return None

    event_bus.subscribe(EventType.WRITEOFF_REQUESTED, "metrics_handler", handler)
    await event_bus.publish(tenant_id=uuid.uuid4(), event_type=EventType.WRITEOFF_REQUESTED, source="test", payload={})

    metrics = EventWorkerMetrics()
    worker = EventWorker(event_bus, metrics=metrics)
    await worker.tick()

    snapshot = metrics.snapshot()
    assert snapshot["events_processed"] >= 1
    assert snapshot["ticks"] >= 1
    assert EventType.WRITEOFF_REQUESTED in snapshot["per_event_type"]
    assert snapshot["per_event_type"][EventType.WRITEOFF_REQUESTED]["succeeded"] >= 1


async def test_subscribed_event_types_reflects_real_subscriptions(event_bus: EventBus) -> None:
    async def handler(event: Event) -> None:
        return None

    event_bus.subscribe(EventType.PAYMENT_REFUNDED, "subscribed_types_handler", handler)
    assert EventType.PAYMENT_REFUNDED in event_bus.subscribed_event_types()
