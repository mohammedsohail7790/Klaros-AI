"""The Klaros Event Worker (Phase 8) — a continuous consumer of the durable
event store, distinct from the Temporal worker (`app/workers/main.py`, which
runs long-lived business *workflows*). This worker's only job is: turn a
published `Event` row into real handler side effects, continuously, without
anyone calling `POST /api/v1/events/process/{event_type}` by hand.

It deliberately does not reimplement delivery, retry, idempotency, or
dead-lettering — all of that already lives in `EventBus.process_pending` /
`EventBus._handle_one` (see app/events/bus.py) and is reused as-is. This
module only adds the "continuously" part: a poll loop, graceful shutdown,
and metrics.

Concurrency safety across multiple worker processes is provided by the
transport, not by this class: Redis Streams consumer groups (`XREADGROUP`)
hand each pending message to exactly one consumer in the group until it is
acked or reclaimed, which is the same "only one worker touches this row"
guarantee `SELECT ... FOR UPDATE SKIP LOCKED` gives a Postgres-polling
design — this repository already has a working Redis Streams transport
(`RedisStreamTransport`), so a second polling mechanism is not introduced.
`InMemoryTransport` (the dev/test fallback documented in
app/events/transport.py) is single-process by construction and provides no
such guarantee — this is a limitation of running without Redis, not of this
worker, and is called out plainly in PROJECT_STATUS.md.
"""

import asyncio
import signal
import time

import structlog

from app.events.bus import EventBus
from app.events.metrics import EventWorkerMetrics, worker_metrics

logger = structlog.get_logger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.0


class EventWorker:
    def __init__(
        self,
        bus: EventBus,
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = 10,
        metrics: EventWorkerMetrics | None = None,
        on_tick=None,
        db_lock: asyncio.Lock | None = None,
    ) -> None:
        self._bus = bus
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self.metrics = metrics if metrics is not None else worker_metrics
        # Optional hook run once per tick after event processing (used by the
        # Morning Brief scheduler — see app/services/morning_brief_service.py
        # — so it piggybacks on this already-running loop instead of adding a
        # second one).
        self._on_tick = on_tick
        # Optional: serialize this worker's DB transactions against some
        # other concurrent caller's. Real multi-connection concurrency
        # (Postgres, or SQLite via separate connections) needs no such
        # thing — safety there comes from the transport/row-locking, not an
        # in-process lock (see the module docstring). This exists only for
        # the specific SQLite `StaticPool` test setup (one shared physical
        # connection for the whole process — see app/db/session.py), where
        # two genuinely-concurrent asyncio tasks committing/rolling back on
        # that one connection can corrupt each other's transaction state.
        # The Phase 8 E2E test (tests/test_phase8_e2e.py) passes one in for
        # exactly this reason; production code never sets it.
        self._db_lock = db_lock

    async def tick(self) -> dict[str, object]:
        """Process every currently-pending event across every subscribed
        event type, once. Returns per-event-type stats. Safe to call
        concurrently with itself (each event/handler pair is guarded by the
        unique `EventProcessingRecord` row — see EventBus._handle_one).
        """
        if self._db_lock is not None:
            async with self._db_lock:
                return await self._tick_unlocked()
        return await self._tick_unlocked()

    async def _tick_unlocked(self) -> dict[str, object]:
        results: dict[str, object] = {}
        # Outbox-relay pass (Phase 12 production hardening) — cheap (single
        # indexed SELECT, capped at 100 rows) and safe to run every tick; see
        # EventBus.reconcile_stuck_events for exactly what gap this closes.
        reconciled = await self._bus.reconcile_stuck_events()
        if reconciled:
            logger.warning("event_worker_reconciled_stuck_events", count=reconciled)
        for event_type in self._bus.subscribed_event_types():
            start = time.monotonic()
            stats = await self._bus.process_pending(event_type, count=self._batch_size)
            duration_ms = (time.monotonic() - start) * 1000
            self.metrics.record_tick(event_type, stats, duration_ms)
            results[event_type] = stats
            if stats.read:
                logger.info(
                    "event_worker_tick",
                    event_type=event_type,
                    read=stats.read,
                    succeeded=stats.succeeded,
                    failed_retrying=stats.failed_retrying,
                    dead_lettered=stats.dead_lettered,
                    duplicates_skipped=stats.duplicates_skipped,
                )
        if self._on_tick is not None:
            try:
                await self._on_tick()
            except Exception as exc:  # noqa: BLE001 — a scheduler hiccup must never kill the worker loop
                logger.error("event_worker_on_tick_hook_failed", error=str(exc))
        return results

    async def run_forever(self, shutdown_event: asyncio.Event | None = None) -> None:
        """The continuous loop. Runs until `shutdown_event` is set (or,
        standalone, until SIGTERM/SIGINT — see main() below). Restart
        recovery needs no special code here: state lives in Postgres
        (`Event.status`, `EventProcessingRecord.attempts`), so a fresh
        process just resumes polling and picks up wherever the durable
        record left off — an event read but not yet acked when the previous
        worker died is still pending in the transport for the next poll.
        """
        shutdown_event = shutdown_event or asyncio.Event()
        self.metrics.record_start()
        logger.info(
            "event_worker_started",
            poll_interval_seconds=self._poll_interval_seconds,
            batch_size=self._batch_size,
        )
        try:
            while not shutdown_event.is_set():
                try:
                    await self.tick()
                except Exception as exc:  # noqa: BLE001 — one bad tick must never crash the worker
                    logger.error("event_worker_tick_failed", error=str(exc))
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=self._poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("event_worker_stopped", ticks=self.metrics.ticks)


async def _standalone_main() -> None:
    """Entry point for running the worker as its own OS process — what the
    `event-worker` Docker Compose service runs
    (`command: python -m app.events.worker`) against the real Redis
    transport. Not used when EVENT_TRANSPORT=memory; see
    app/main.py's lifespan, which starts an in-process EventWorker task
    instead in that dev-fallback mode (InMemoryTransport state only exists
    inside one process, so a separate OS process couldn't see it anyway).
    """
    from app.api.tool_deps import get_morning_brief_service, get_wired_event_bus
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    configure_logging()
    settings = get_settings()
    bus = get_wired_event_bus()
    morning_brief_service = get_morning_brief_service()

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            # add_signal_handler is unavailable on some platforms (e.g. Windows);
            # the worker still exits via KeyboardInterrupt in that case.
            pass

    worker = EventWorker(
        bus,
        poll_interval_seconds=settings.EVENT_WORKER_POLL_SECONDS,
        on_tick=morning_brief_service.check_and_generate_scheduled,
    )
    await worker.run_forever(shutdown_event)


if __name__ == "__main__":
    asyncio.run(_standalone_main())
