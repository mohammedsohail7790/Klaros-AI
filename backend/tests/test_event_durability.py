"""Event durability: what happens when a worker or the transport dies at each point of an event's life, and that recovery never repeats a business effect.

Crash points covered: after the transport read but before the ack; after the claim (record + PROCESSING committed) but before the handler finished;
after the handler's side effect but before SUCCESS was committed; Redis unavailable at publish time. Plus: concurrent delivery of one event, retry /
dead-letter / replay, restart of a worker, the worker's start-up refusals, its heartbeat and health port, and the real Halla escalation (human handoff)
path. The Redis tests need a real Redis (REDIS_URL, like tests/test_redis_stream_transport.py); everything else runs on SQLite with the in-memory transport.

Honest semantics under test: delivery is AT LEAST ONCE. A handler may run twice after a crash that hit between its side effect and its SUCCESS commit;
"exactly once" holds for BUSINESS EFFECTS only where the handler is idempotent. The handoff workflow dispatch is (keyed on automation version + source
event), and the test below proves one run and one notification after the crash."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport, RedisStreamTransport
from app.events.metrics import EventWorkerMetrics
from app.events.worker import HEARTBEAT_KEY, EventWorker
from app.models.event import DeadLetterEvent, Event, EventProcessingRecord, EventStatus, ProcessingStatus
from tests.test_halla_integration import halla  # noqa: F401  (fixture)

ETYPE = "test.durability"


def _patch_settings(monkeypatch, **values) -> None:
    """`app.main` captured its settings object at import; another test may have cleared the `get_settings` cache since, leaving two different objects.
    Patch both so these tests do not depend on test order."""
    import app.main as main_module

    for name, value in values.items():
        monkeypatch.setattr(get_settings(), name, value)
        monkeypatch.setattr(main_module.settings, name, value)


def _bus(transport=None, **kw) -> EventBus:
    return EventBus(session_factory=async_session_maker, transport=transport or InMemoryTransport(), retry_backoff_seconds=0, **kw)


async def _publish(bus: EventBus, tenant: uuid.UUID, key: str | None = None) -> Event:
    return await bus.publish(tenant_id=tenant, event_type=ETYPE, source="test", payload={"n": 1}, idempotency_key=key)


async def _record(tenant, event_id, handler="h") -> EventProcessingRecord | None:
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant)
        return (await s.execute(select(EventProcessingRecord).where(EventProcessingRecord.event_id == event_id, EventProcessingRecord.handler_name == handler))).scalar_one_or_none()


async def _event(tenant, event_id) -> Event:
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant)
        return await s.get(Event, event_id)


async def _age_record(tenant, event_id, seconds: int, handler="h") -> None:
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant)
        await s.execute(update(EventProcessingRecord).where(EventProcessingRecord.event_id == event_id, EventProcessingRecord.handler_name == handler)
                        .values(last_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=seconds)))
        await s.commit()


class Effects:
    """An idempotent business effect (keyed on the event id) plus a raw run counter, so the tests can tell 'ran twice' from 'effect applied twice'."""

    def __init__(self) -> None:
        self.runs = 0
        self.applied: set[uuid.UUID] = set()

    async def handler(self, event: Event) -> None:
        self.runs += 1
        self.applied.add(event.id)


# ------------------------------------------------------------------------------------------------ crash after the claim
async def test_a_worker_that_dies_after_claiming_an_event_is_recovered_once_the_lease_expires() -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus = _bus(processing_lease_seconds=0.3)
    bus.subscribe(ETYPE, "h", fx.handler)
    ev = await _publish(bus, tenant)

    async def die_after_effect(event: Event) -> None:
        await fx.handler(event)
        raise asyncio.CancelledError()                         # a BaseException: the process is gone before SUCCESS is committed

    bus._subscriptions[ETYPE][0].handler = die_after_effect
    with pytest.raises(asyncio.CancelledError):
        await bus.process_pending(ETYPE)
    rec = await _record(tenant, ev.id)
    assert rec.status == ProcessingStatus.FAILED and (await _event(tenant, ev.id)).status == EventStatus.PROCESSING       # stranded exactly as a crash leaves it

    # the restarted worker: inside the lease it must NOT start a second run (another worker might still be on it) ...
    bus2 = _bus(transport=bus.transport, processing_lease_seconds=0.3)
    bus2.subscribe(ETYPE, "h", fx.handler)
    worker = EventWorker(bus2)
    await worker.tick()
    assert fx.runs == 1
    # ... after the lease expires the relay re-sends it and the worker takes it over
    await asyncio.sleep(0.4)
    await worker.tick()
    await worker.tick()
    assert (await _record(tenant, ev.id)).status == ProcessingStatus.SUCCESS and (await _event(tenant, ev.id)).status == EventStatus.PROCESSED
    assert fx.applied == {ev.id}                                # the business effect (idempotent handler) happened once ...
    assert fx.runs == 2                                         # ... though the handler body ran twice: at-least-once, stated plainly
    await worker.tick()
    assert fx.runs == 2                                         # and a finished event is never run again


async def test_an_unfinished_event_inside_its_lease_is_left_to_its_owner() -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus = _bus(processing_lease_seconds=300)
    bus.subscribe(ETYPE, "h", fx.handler)
    ev = await _publish(bus, tenant)
    async with async_session_maker() as s:                      # another worker claimed it a moment ago
        await set_tenant_context(s, tenant)
        s.add(EventProcessingRecord(tenant_id=tenant, event_id=ev.id, handler_name="h", status=ProcessingStatus.FAILED, attempts=1, last_attempt_at=datetime.now(timezone.utc)))
        await s.commit()
    stats = await bus.process_pending(ETYPE)
    assert fx.runs == 0 and stats.duplicates_skipped == 1
    assert await bus.reconcile_stuck_events() == 0              # not stranded yet: nothing is re-sent
    await _age_record(tenant, ev.id, 400)                       # its owner died: the lease has expired
    assert await bus.reconcile_stuck_events() == 1
    await bus.process_pending(ETYPE)
    assert fx.runs == 1 and (await _record(tenant, ev.id)).status == ProcessingStatus.SUCCESS


async def test_two_simultaneous_deliveries_of_an_abandoned_event_run_the_handler_once() -> None:
    tenant, runs = uuid.uuid4(), []
    bus = _bus(processing_lease_seconds=1)

    async def slow(event: Event) -> None:
        runs.append(event.id)
        await asyncio.sleep(0.05)

    bus.subscribe(ETYPE, "h", slow)
    ev = await _publish(bus, tenant)
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant)
        s.add(EventProcessingRecord(tenant_id=tenant, event_id=ev.id, handler_name="h", status=ProcessingStatus.FAILED, attempts=1, last_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=60)))
        await s.commit()
    sub = bus._subscriptions[ETYPE][0]
    results = await asyncio.gather(*[bus._handle_one(ev.id, sub) for _ in range(4)])
    assert len(runs) == 1, runs
    assert sorted(str(r) for r in results).count(str(ProcessingStatus.SUCCESS)) == 1


# ------------------------------------------------------------------------------------------------ retry, dead letter, replay
async def test_retries_dead_letter_once_and_a_redelivered_dead_letter_is_not_rerun() -> None:
    tenant, calls = uuid.uuid4(), []
    bus = _bus(max_retries=3)
    broken = {"on": True}

    async def flaky(event: Event) -> None:
        calls.append(event.id)
        if broken["on"]:
            raise RuntimeError("downstream outage")

    bus.subscribe(ETYPE, "h", flaky)
    ev = await _publish(bus, tenant)
    stats = await bus.process_pending(ETYPE)
    assert stats.dead_lettered == 1 and len(calls) == 3        # bounded retries, then terminal
    # the same event delivered again (duplicate message / relay): no new run, no second dead-letter row
    await bus.transport.send(bus._stream_name(ETYPE), {"event_id": str(ev.id), "tenant_id": str(tenant), "event_type": ETYPE, "correlation_id": str(uuid.uuid4())})
    stats = await bus.process_pending(ETYPE)
    assert len(calls) == 3 and stats.duplicates_skipped == 1
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant)
        assert (await s.execute(select(func.count()).select_from(DeadLetterEvent).where(DeadLetterEvent.event_id == ev.id))).scalar() == 1
    assert await bus.reconcile_stuck_events() == 0              # a dead-lettered event is never resurrected by the relay
    broken["on"] = False                                         # the outage is over; an operator replays it
    assert await bus.replay(ev.id, "h") == ProcessingStatus.SUCCESS
    assert (await _event(tenant, ev.id)).status == EventStatus.PROCESSED


async def test_a_handler_that_succeeds_on_retry_leaves_one_success_and_no_dead_letter() -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus = _bus(max_retries=3)
    seen = {"n": 0}

    async def second_time_lucky(event: Event) -> None:
        seen["n"] += 1
        if seen["n"] < 2:
            raise RuntimeError("transient")
        await fx.handler(event)

    bus.subscribe(ETYPE, "h", second_time_lucky)
    ev = await _publish(bus, tenant)
    stats = await bus.process_pending(ETYPE)
    assert stats.succeeded == 1 and fx.applied == {ev.id} and (await _record(tenant, ev.id)).attempts == 2


async def test_duplicate_publish_with_the_same_idempotency_key_is_one_event_and_one_effect() -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus = _bus()
    bus.subscribe(ETYPE, "h", fx.handler)
    a, b = await _publish(bus, tenant, "dup-key-1"), await _publish(bus, tenant, "dup-key-1")
    assert a.id == b.id
    await bus.process_pending(ETYPE)
    await bus.process_pending(ETYPE)
    assert fx.runs == 1


# ------------------------------------------------------------------------------------------------ transport outage at publish time
async def test_an_event_published_while_the_transport_is_down_is_stored_and_delivered_later() -> None:
    tenant, fx = uuid.uuid4(), Effects()
    transport = InMemoryTransport()
    bus = _bus(transport=transport)
    bus.subscribe(ETYPE, "h", fx.handler)
    real_send, down = transport.send, {"on": True}

    async def flaky_send(stream, fields):
        if down["on"]:
            raise ConnectionError("redis unavailable")
        return await real_send(stream, fields)

    transport.send = flaky_send                                   # type: ignore[method-assign]
    ev = await _publish(bus, tenant)                              # must NOT raise: the event is durable in Postgres
    assert (await _event(tenant, ev.id)).status == EventStatus.PUBLISHED
    assert (await bus.process_pending(ETYPE)).read == 0 and fx.runs == 0
    down["on"] = False
    async with async_session_maker() as s:                        # the relay's grace period has passed
        await set_tenant_context(s, tenant)
        await s.execute(update(Event).where(Event.id == ev.id).values(created_at=datetime.now(timezone.utc) - timedelta(seconds=120)))
        await s.commit()
    assert await bus.reconcile_stuck_events() == 1
    await bus.process_pending(ETYPE)
    assert fx.applied == {ev.id}


# ------------------------------------------------------------------------------------------------ worker guards, heartbeat, health port
async def test_a_separate_worker_refuses_the_in_memory_transport(monkeypatch) -> None:
    from app.events import worker as worker_module

    monkeypatch.setattr(get_settings(), "EVENT_TRANSPORT", "memory")
    with pytest.raises(SystemExit) as exc:
        await asyncio.wait_for(worker_module._standalone_main(), timeout=15)    # a regression that starts the worker would otherwise run forever
    assert exc.value.code == 2


async def test_a_worker_refuses_to_start_when_redis_is_unreachable_and_never_prints_the_url(monkeypatch, capsys) -> None:
    from app.events import worker as worker_module

    monkeypatch.setattr(get_settings(), "EVENT_TRANSPORT", "redis")
    monkeypatch.setattr(get_settings(), "REDIS_URL", "redis://user:topsecretpassword@127.0.0.1:1/0")
    with pytest.raises(SystemExit) as exc:
        await asyncio.wait_for(worker_module._standalone_main(), timeout=15)    # a regression that starts the worker would otherwise run forever
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "topsecretpassword" not in out.out + out.err


async def test_the_worker_health_port_reports_stalled_until_it_ticks(monkeypatch) -> None:
    from app.events import worker as worker_module

    sock_port = _free_port()
    monkeypatch.setenv("EVENT_WORKER_HEALTH_PORT", str(sock_port))
    bus = _bus()
    bus.subscribe(ETYPE, "h", Effects().handler)
    worker = EventWorker(bus, metrics=EventWorkerMetrics())          # its own counters: the module-level default is shared across tests
    server = await worker_module._start_health_server(worker, bus)
    try:
        status, body = await _http_get(sock_port)
        assert status == 503 and body["status"] == "stalled"
        await worker.tick()
        status, body = await _http_get(sock_port)
        assert status == 200 and body["status"] == "ok" and body["ticks"] >= 1
        assert set(body) <= {"status", "last_tick_age_seconds", "ticks", "events_processed", "events_dead_lettered"}      # counters only
    finally:
        server.close()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_get(port: int) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


# ------------------------------------------------------------------------------------------------ the real human-handoff path
async def test_a_crash_during_the_halla_escalation_handoff_still_produces_exactly_one_workflow_run_and_one_notification(client, halla, event_bus) -> None:  # noqa: F811
    from app.models.event import EventType
    from tests.test_halla_integration import BASE, _body, _connected, _deliver, _h, _lead

    token, tid = await _connected(client, "Dur Handoff", "durhandoff@example.com")
    assert (await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))).json()["created"] is True
    lead = await _lead(client, token)
    assert (await _deliver(client, tid, _body("lead.escalated", eid="evt-dur-1", data={"klaros_lead_id": lead, "summary": "Asked for a person."}))).json() == {"status": "ok"}

    # the worker runs the automation handler, the workflow dispatch happens, and the process dies before SUCCESS is committed
    crashed = {"n": 0}
    for sub in event_bus._subscriptions[EventType.HALLA_LEAD_ESCALATED]:
        real = sub.handler

        async def crash_once(event, _real=real):
            await _real(event)
            if crashed["n"] == 0:
                crashed["n"] += 1
                raise asyncio.CancelledError()

        sub.handler = crash_once
    event_bus.processing_lease_seconds = 0.3
    with pytest.raises(asyncio.CancelledError):
        await event_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)

    await asyncio.sleep(0.4)                                      # lease expires; the restarted worker's relay re-sends and takes it over
    worker = EventWorker(event_bus)
    for _ in range(3):
        await worker.tick()
    # Halla re-delivering the same event is a duplicate at the receiver
    assert (await _deliver(client, tid, _body("lead.escalated", eid="evt-dur-1", data={"klaros_lead_id": lead, "summary": "Asked for a person."}))).json() == {"status": "duplicate_ignored"}
    await worker.tick()

    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    wf = next(w for w in ops["workflows"] if w["name"] == "Escalated lead alert")
    assert wf["runs"] == 1 and wf["last_run"]["status"] == "COMPLETED"                                                       # one handoff workflow run, not two
    notes = (await client.get("/api/v1/notifications", headers=_h(token))).json()["notifications"]
    assert len([n for n in notes if n["title"] == "A lead needs a person"]) == 1                                              # one handoff notification
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert [e["type"] for e in inter["events"]] == ["halla.lead.escalated"]                                                   # one escalation event on the lead


async def _escalation_setup(client, event_bus, name, email, eid):
    from tests.test_halla_integration import BASE, _body, _connected, _deliver, _h, _lead

    token, tid = await _connected(client, name, email)
    assert (await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))).json()["created"] is True
    lead = await _lead(client, token)
    assert (await _deliver(client, tid, _body("lead.escalated", eid=eid, data={"klaros_lead_id": lead, "summary": "Asked for a person."}))).json() == {"status": "ok"}
    return token, tid, lead


async def test_a_transient_failure_starting_the_handoff_workflow_is_retried_not_swallowed(client, halla, event_bus, monkeypatch) -> None:  # noqa: F811
    from app.models.event import EventType
    from app.services.automation_service import AutomationService
    from tests.test_halla_integration import BASE, _h

    token, tid, lead = await _escalation_setup(client, event_bus, "Dur Transient", "durtransient@example.com", "evt-dur-t1")
    real, calls = AutomationService.start_execution, {"n": 0}

    async def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database blip")
        return await real(self, *a, **k)

    monkeypatch.setattr(AutomationService, "start_execution", flaky)
    stats = await event_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    assert stats.succeeded >= 1 and calls["n"] >= 2                                      # it was retried; previously the first failure marked the event processed
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    wf = next(w for w in ops["workflows"] if w["name"] == "Escalated lead alert")
    assert wf["runs"] == 1 and wf["last_run"]["status"] == "COMPLETED"
    notes = (await client.get("/api/v1/notifications", headers=_h(token))).json()["notifications"]
    assert len([n for n in notes if n["title"] == "A lead needs a person"]) == 1


async def test_a_permanent_failure_starting_the_handoff_workflow_is_dead_lettered_visibly(client, halla, event_bus, monkeypatch) -> None:  # noqa: F811
    from app.models.event import EventType
    from app.services.automation_service import AutomationService

    token, tid, lead = await _escalation_setup(client, event_bus, "Dur Permanent", "durpermanent@example.com", "evt-dur-p1")

    async def broken(self, *a, **k):
        raise RuntimeError("workflow engine unavailable")

    monkeypatch.setattr(AutomationService, "start_execution", broken)
    stats = await event_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    assert stats.dead_lettered >= 1                                                       # the automation_dispatch handler is dead-lettered, no longer reported as processed
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        rows = (await s.execute(select(DeadLetterEvent).where(DeadLetterEvent.tenant_id == tid, DeadLetterEvent.event_type == EventType.HALLA_LEAD_ESCALATED))).scalars().all()
    assert len(rows) >= 1 and "automation dispatch failed" in rows[0].reason
    monkeypatch.undo()
    assert await event_bus.replay(rows[0].event_id, rows[0].handler_name) == ProcessingStatus.SUCCESS     # an operator can replay it once the engine is back


# ------------------------------------------------------------------------------------------------ real Redis Streams
def _redis_ok() -> bool:
    try:
        import redis as sync_redis

        sync_redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=2).ping()
        return get_settings().EVENT_TRANSPORT != "memory" or True
    except Exception:  # noqa: BLE001
        return False


requires_real_redis = pytest.mark.skipif(not _redis_ok(), reason="requires a real Redis >= 6.2 via REDIS_URL")


@pytest.fixture
async def redis_bus_factory():
    import redis.asyncio as redis

    clients, prefixes = [], []

    def make(consumer="worker-1", **kw) -> tuple[EventBus, "redis.Redis"]:
        client = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        clients.append(client)
        prefix = kw.pop("stream_prefix", None) or f"klaros.test.{uuid.uuid4().hex[:8]}"
        prefixes.append(prefix)
        bus = EventBus(session_factory=async_session_maker, transport=RedisStreamTransport(client), stream_prefix=prefix, consumer_name=consumer, retry_backoff_seconds=0, **kw)
        return bus, client

    yield make
    cleaner = clients[0] if clients else None
    if cleaner is not None:
        for p in set(prefixes):
            async for key in cleaner.scan_iter(match=f"{p}.*"):
                await cleaner.delete(key)
    for c in clients:
        await c.aclose()


@requires_real_redis
async def test_redis_a_message_read_by_a_crashed_consumer_is_taken_over_and_processed_once(redis_bus_factory) -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus_a, redis_a = redis_bus_factory("worker-A", pending_reclaim_idle_ms=0)
    bus_a.subscribe(ETYPE, "h", fx.handler)
    ev = await _publish(bus_a, tenant)
    stream = bus_a._stream_name(ETYPE)
    await bus_a.transport.ensure_group(stream, "h")
    got = await bus_a.transport.read_group(stream, "h", "worker-A", count=10)          # worker A reads ... and the process dies before the ack
    assert len(got) == 1 and await bus_a.transport.pending_count(stream, "h") == 1
    assert await bus_a.transport.read_group(stream, "h", "worker-B", count=10) == []   # a plain read never returns it again: the original gap

    bus_b = EventBus(session_factory=async_session_maker, transport=RedisStreamTransport(redis_a), stream_prefix=bus_a.stream_prefix, consumer_name="worker-B",
                     retry_backoff_seconds=0, pending_reclaim_idle_ms=0)
    bus_b.subscribe(ETYPE, "h", fx.handler)
    stats = await bus_b.process_pending(ETYPE)
    assert stats.succeeded == 1 and fx.applied == {ev.id} and fx.runs == 1
    assert await bus_b.transport.pending_count(stream, "h") == 0                       # acked: nothing left pending
    assert (await bus_b.process_pending(ETYPE)).read == 0 and fx.runs == 1


@requires_real_redis
async def test_redis_a_message_is_not_taken_over_before_it_has_been_idle_long_enough(redis_bus_factory) -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus_a, redis_a = redis_bus_factory("worker-A", pending_reclaim_idle_ms=60_000)
    bus_a.subscribe(ETYPE, "h", fx.handler)
    await _publish(bus_a, tenant)
    stream = bus_a._stream_name(ETYPE)
    await bus_a.transport.ensure_group(stream, "h")
    await bus_a.transport.read_group(stream, "h", "worker-A", count=10)                # being worked on right now by A
    bus_b = EventBus(session_factory=async_session_maker, transport=RedisStreamTransport(redis_a), stream_prefix=bus_a.stream_prefix, consumer_name="worker-B",
                     retry_backoff_seconds=0, pending_reclaim_idle_ms=60_000)
    bus_b.subscribe(ETYPE, "h", fx.handler)
    assert (await bus_b.process_pending(ETYPE)).read == 0 and fx.runs == 0             # only genuinely idle messages are reclaimed


@requires_real_redis
async def test_redis_a_restarted_worker_resumes_the_backlog_without_losing_or_repeating_anything(redis_bus_factory) -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus_a, redis_a = redis_bus_factory("worker-1", pending_reclaim_idle_ms=0)
    bus_a.subscribe(ETYPE, "h", fx.handler)
    events = [await _publish(bus_a, tenant) for _ in range(6)]                        # published while no worker is running
    await bus_a.process_pending(ETYPE, count=2)                                        # worker A handles two, then is killed
    assert len(fx.applied) == 2
    bus_b = EventBus(session_factory=async_session_maker, transport=RedisStreamTransport(redis_a), stream_prefix=bus_a.stream_prefix, consumer_name="worker-1",
                     retry_backoff_seconds=0, pending_reclaim_idle_ms=0)
    bus_b.subscribe(ETYPE, "h", fx.handler)                                            # same consumer name after a restart, new process
    worker = EventWorker(bus_b, batch_size=10)
    for _ in range(3):
        await worker.tick()
    assert fx.applied == {e.id for e in events} and fx.runs == 6                       # all six, each effect once
    assert await bus_b.transport.pending_count(bus_b._stream_name(ETYPE), "h") == 0


@requires_real_redis
async def test_redis_worker_heartbeat_is_written_and_expires(redis_bus_factory) -> None:
    bus, client = redis_bus_factory()
    bus.subscribe(ETYPE, "h", Effects().handler)
    key = f"{HEARTBEAT_KEY}.{uuid.uuid4().hex[:6]}"
    assert await bus.transport.heartbeat_age_seconds(key) is None                      # no worker has ever ticked
    await bus.transport.set_heartbeat(key, 1)
    age = await bus.transport.heartbeat_age_seconds(key)
    assert age is not None and age < 2
    await asyncio.sleep(1.3)
    assert await bus.transport.heartbeat_age_seconds(key) is None                      # a dead worker's heartbeat expires
    await client.delete(key)


@requires_real_redis
async def test_redis_ready_reports_the_worker_state_without_failing_readiness(client, redis_bus_factory, monkeypatch) -> None:
    from app.events import factory

    bus, _ = redis_bus_factory()
    bus.subscribe(ETYPE, "h", Effects().handler)
    _patch_settings(monkeypatch, EVENT_TRANSPORT="redis")
    monkeypatch.setattr(factory, "get_event_bus", lambda: bus)
    # fresh stream prefix is unrelated to the heartbeat key, so clear it to make the first assertion deterministic
    await bus.transport._redis.delete(HEARTBEAT_KEY)
    r = await client.get("/ready")
    assert r.status_code == 200 and r.json()["event_worker"].startswith("no_heartbeat")        # visible, but the API itself stays ready
    await EventWorker(bus).tick()
    r = await client.get("/ready")
    assert r.status_code == 200 and r.json()["event_worker"].startswith("ok")
    await bus.transport._redis.delete(HEARTBEAT_KEY)


@requires_real_redis
async def test_redis_a_delivery_blocked_by_a_live_lease_stays_pending_and_is_taken_over_when_the_lease_expires(redis_bus_factory) -> None:
    tenant, fx = uuid.uuid4(), Effects()
    bus, _ = redis_bus_factory("worker-B", pending_reclaim_idle_ms=0, processing_lease_seconds=300)
    bus.subscribe(ETYPE, "h", fx.handler)
    ev = await _publish(bus, tenant)
    async with async_session_maker() as s:                      # worker A is mid-run: record + fresh lease, then it dies without finishing
        await set_tenant_context(s, tenant)
        s.add(EventProcessingRecord(tenant_id=tenant, event_id=ev.id, handler_name="h", status=ProcessingStatus.FAILED, attempts=1, last_attempt_at=datetime.now(timezone.utc)))
        await s.commit()
    stream = bus._stream_name(ETYPE)
    stats = await bus.process_pending(ETYPE)
    assert fx.runs == 0 and stats.duplicates_skipped == 1
    assert await bus.transport.pending_count(stream, "h") == 1                           # NOT acked: the only copy of the message is kept
    await bus.process_pending(ETYPE)
    assert fx.runs == 0                                                                  # retried on later ticks, still inside the lease: still no second run
    await _age_record(tenant, ev.id, 400)                                                # A is presumed dead
    stats = await bus.process_pending(ETYPE)                                             # no relay needed: the pending message itself is the recovery path
    assert stats.succeeded == 1 and fx.applied == {ev.id} and fx.runs == 1
    assert await bus.transport.pending_count(stream, "h") == 0


@requires_real_redis
async def test_redis_the_worker_can_run_inside_the_api_process_when_explicitly_enabled_and_it_heartbeats(monkeypatch, redis_bus_factory) -> None:
    import redis.asyncio as redis

    import app.api.tool_deps as tool_deps
    from app.main import app

    _patch_settings(monkeypatch, EVENT_TRANSPORT="redis", EVENT_WORKER_IN_PROCESS=True)
    real_bus = tool_deps.get_wired_event_bus()                      # the wired handlers; only the transport is swapped for the real Redis one
    redis_bus, _ = redis_bus_factory()
    redis_bus._subscriptions = real_bus._subscriptions
    monkeypatch.setattr(tool_deps, "get_wired_event_bus", lambda: redis_bus)
    client = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    await client.delete(HEARTBEAT_KEY)
    try:
        async with app.router.lifespan_context(app):
            for _ in range(40):
                if await client.get(HEARTBEAT_KEY):
                    break
                await asyncio.sleep(0.25)
            assert await client.get(HEARTBEAT_KEY)                                  # the in-process worker is ticking against Redis
    finally:
        await client.delete(HEARTBEAT_KEY)
        await client.aclose()


async def test_the_worker_is_not_started_in_the_api_process_by_default_on_redis(monkeypatch) -> None:
    from app.main import app

    _patch_settings(monkeypatch, EVENT_TRANSPORT="redis", EVENT_WORKER_IN_PROCESS=False)
    started = []
    import app.events.worker as w

    real = w.EventWorker.run_forever

    async def spy(self, *a, **k):
        started.append(1)
        return await real(self, *a, **k)

    monkeypatch.setattr(w.EventWorker, "run_forever", spy)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
    assert started == []                                                              # production shape: a separate worker service consumes the stream
