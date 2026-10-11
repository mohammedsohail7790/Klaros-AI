"""The event bus (section 1/2).

publish() is durability-first: it writes to Postgres before it ever touches
the transport, so an event is never lost even if Redis is down at publish
time (the row exists; a later replay can still deliver it).

Duplicate delivery is handled at two levels:
  1. publish-time dedup via the (tenant_id, idempotency_key) unique
     constraint on `events` — publishing the same idempotency_key twice
     returns the original event instead of creating a second one.
  2. handler-level dedup via `event_processing_records` — even if the same
     event is delivered to a handler twice (retried webhook, re-read stream
     message, explicit replay), the handler body only runs once per
     (event_id, handler_name); a second delivery is detected and skipped.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.transport import EventTransport
from app.models.event import DeadLetterEvent, Event, EventProcessingRecord, EventStatus, ProcessingStatus

logger = structlog.get_logger(__name__)

# Returned by `_handle_one` when another worker's run of this (event, handler) is still inside its lease: not done, not failed, not for this delivery.
LEASE_HELD = "LEASE_HELD"

EventHandler = Callable[[Event], Awaitable[None]]


@dataclass
class ProcessingStats:
    read: int = 0
    succeeded: int = 0
    failed_retrying: int = 0
    dead_lettered: int = 0
    duplicates_skipped: int = 0


@dataclass
class _Subscription:
    handler_name: str
    handler: EventHandler


@dataclass(eq=False)
class EventBus:
    """`eq=False` keeps the default identity-based `__eq__`/`__hash__` (a
    plain `@dataclass` generates `__eq__` and, as a side effect, sets
    `__hash__` to None) — Phase 25 needs EventBus instances usable as
    `functools.lru_cache` keys (see app/api/tool_deps.py's
    `get_morning_brief_service`), and no code anywhere compares two
    EventBus instances for value equality; identity is the only
    meaningful notion of "same bus" here."""

    session_factory: async_sessionmaker
    transport: EventTransport
    stream_prefix: str = "klaros.events"
    max_retries: int = 3
    retry_backoff_seconds: float = 0.0
    consumer_name: str = "worker-1"
    # A message delivered to a consumer and never acked for this long belongs to a crashed worker and is taken over (Redis XAUTOCLAIM).
    pending_reclaim_idle_ms: int = 60_000
    # A handler run (one (event, handler) record) that has not finished within this long is presumed dead: another delivery may take it over.
    # Until then a concurrent delivery of the same event does NOT run the handler a second time.
    processing_lease_seconds: float = 300.0
    _subscriptions: dict[str, list[_Subscription]] = field(default_factory=dict)

    def subscribe(self, event_type: str, handler_name: str, handler: EventHandler) -> None:
        self._subscriptions.setdefault(event_type, []).append(_Subscription(handler_name, handler))

    def subscribed_event_types(self) -> list[str]:
        """Every event type with at least one registered handler — what a
        continuous worker needs to poll. Publishing an event type with no
        subscribers is legal (nothing consumes it, by design), so this is
        deliberately *not* the full `EventType` enum.
        """
        return list(self._subscriptions.keys())

    def _stream_name(self, event_type: str) -> str:
        return f"{self.stream_prefix}.{event_type}"

    async def publish(
        self,
        *,
        tenant_id: uuid.UUID,
        event_type: str,
        source: str,
        payload: dict,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> Event:
        correlation_id = correlation_id or uuid.uuid4()

        async with self.session_factory() as session:
            await set_tenant_context(session, tenant_id)
            if idempotency_key:
                existing = (
                    await session.execute(
                        select(Event).where(
                            Event.tenant_id == tenant_id, Event.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    logger.info(
                        "event_publish_deduplicated",
                        event_id=str(existing.id),
                        event_type=event_type,
                        idempotency_key=idempotency_key,
                    )
                    existing.was_deduplicated = True
                    return existing

            event = Event(
                tenant_id=tenant_id,
                event_type=event_type,
                source=source,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                status=EventStatus.PUBLISHED,
            )
            session.add(event)
            try:
                await session.commit()
            except IntegrityError:
                # Race: another request published the same idempotency_key concurrently.
                await session.rollback()
                existing = (
                    await session.execute(
                        select(Event).where(
                            Event.tenant_id == tenant_id, Event.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one()
                existing.was_deduplicated = True
                return existing
            await session.refresh(event)
            event.was_deduplicated = False

        try:
            await self.transport.send(
                self._stream_name(event_type),
                {
                    "event_id": str(event.id),
                    "tenant_id": str(tenant_id),
                    "event_type": event_type,
                    "correlation_id": str(correlation_id),
                },
            )
        except Exception as exc:  # noqa: BLE001 - the event is already durable in Postgres; failing the caller would invite a duplicate retry
            # Delivery is the outbox relay's job from here: `reconcile_stuck_events` re-sends any PUBLISHED event after its grace period.
            logger.warning("event_transport_send_failed_will_reconcile", event_id=str(event.id), event_type=event_type, error_type=type(exc).__name__)
        logger.info(
            "event_published",
            event_id=str(event.id),
            event_type=event_type,
            tenant_id=str(tenant_id),
            correlation_id=str(correlation_id),
        )
        return event

    async def process_pending(self, event_type: str, count: int = 10) -> ProcessingStats:
        stats = ProcessingStats()
        subscriptions = self._subscriptions.get(event_type, [])
        stream = self._stream_name(event_type)

        for sub in subscriptions:
            await self.transport.ensure_group(stream, sub.handler_name)
            # Messages a crashed consumer read but never acked come first (a plain read never returns them again), then new ones.
            stale = await self.transport.claim_stale(
                stream, sub.handler_name, self.consumer_name, self.pending_reclaim_idle_ms, count=count
            )
            fresh = await self.transport.read_group(stream, sub.handler_name, self.consumer_name, count=count)
            seen: set[str] = set()
            messages = [m for m in [*stale, *fresh] if not (m.message_id in seen or seen.add(m.message_id))]
            for message in messages:
                stats.read += 1
                event_id = uuid.UUID(message.fields["event_id"])
                try:
                    message_tenant = uuid.UUID(message.fields["tenant_id"]) if message.fields.get("tenant_id") else None
                except ValueError:
                    message_tenant = None
                result = await self._handle_one(event_id, sub, tenant_id=message_tenant)
                if result == LEASE_HELD:
                    stats.duplicates_skipped += 1
                    if self.transport.keeps_unacked_messages:
                        continue  # leave it pending: once the other worker's lease expires (it died) the next claim_stale takes it over
                elif result == ProcessingStatus.SUCCESS:
                    stats.succeeded += 1
                elif result == ProcessingStatus.DEAD_LETTER:
                    stats.dead_lettered += 1
                elif result is None:
                    stats.duplicates_skipped += 1
                else:
                    stats.failed_retrying += 1
                await self.transport.ack(stream, sub.handler_name, message.message_id)

        return stats

    async def _handle_one(self, event_id: uuid.UUID, sub: _Subscription, *, tenant_id: uuid.UUID | None = None) -> str | None:
        async with self.session_factory() as session:
            # Under real row-level security the event row is invisible until a tenant is set, so a worker that read it by primary key alone found
            # "no such event" for every message. The stream message carries the tenant; stamp it BEFORE the first read. A wrong tenant can only make
            # the event invisible (never visible to the wrong tenant), and the check below refuses a mismatch.
            if tenant_id is not None:
                await set_tenant_context(session, tenant_id)
            event = await session.get(Event, event_id)
            if event is None:
                logger.warning("event_not_found_for_processing", event_id=str(event_id))
                return ProcessingStatus.FAILED

            # Every event belongs to exactly one tenant (see module
            # docstring) — stamp this transaction's tenant context now that
            # the event row (fetched by PK, no tenant filter needed) has
            # told us which tenant, before any further tenant-owned table
            # access below (EventProcessingRecord, DeadLetterEvent) or the
            # handler's own DB work.
            if tenant_id is not None and event.tenant_id != tenant_id:
                logger.error("event_tenant_mismatch_refused", event_id=str(event_id))
                return ProcessingStatus.FAILED
            if tenant_id is None:  # (when the message supplied the tenant it was stamped above, before the read: one stamp per event)
                await set_tenant_context(session, event.tenant_id)

            record = (
                await session.execute(
                    select(EventProcessingRecord).where(
                        EventProcessingRecord.event_id == event_id,
                        EventProcessingRecord.handler_name == sub.handler_name,
                    )
                )
            ).scalar_one_or_none()

            if record is not None and record.status == ProcessingStatus.SUCCESS:
                logger.info(
                    "event_duplicate_delivery_skipped",
                    event_id=str(event_id),
                    handler=sub.handler_name,
                )
                return None

            if record is not None and record.status == ProcessingStatus.DEAD_LETTER:
                # Terminal. A redelivered message must not run the handler again or write a second dead-letter row; `replay()` is the only way back.
                logger.info("event_dead_lettered_delivery_skipped", event_id=str(event_id), handler=sub.handler_name)
                return None

            is_new_record = record is None
            if record is None:
                record = EventProcessingRecord(
                    tenant_id=event.tenant_id,
                    event_id=event_id,
                    handler_name=sub.handler_name,
                    status=ProcessingStatus.FAILED,
                    attempts=0,
                    last_attempt_at=datetime.now(timezone.utc),  # the claim time: the lease starts here
                )
                session.add(record)
            else:
                # An unfinished record exists. It is either another worker's run still in progress (inside its lease: back off, never run the
                # handler concurrently) or a crashed worker's (lease expired: take it over). One atomic compare-and-set decides, across processes.
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.processing_lease_seconds)
                claim = await session.execute(
                    update(EventProcessingRecord)
                    .where(
                        EventProcessingRecord.id == record.id,
                        EventProcessingRecord.status == ProcessingStatus.FAILED,
                        or_(EventProcessingRecord.last_attempt_at.is_(None), EventProcessingRecord.last_attempt_at < cutoff),
                    )
                    .values(last_attempt_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
                if not claim.rowcount:
                    await session.rollback()
                    logger.info("event_processing_lease_held_by_another_worker", event_id=str(event_id), handler=sub.handler_name)
                    return LEASE_HELD
                await session.refresh(record)

            event.status = EventStatus.PROCESSING
            try:
                await session.commit()
            except IntegrityError:
                # Two workers (or two ticks of the same worker, racing under a
                # transport that doesn't itself serialize delivery — see
                # InMemoryTransport's documented single-process limitation in
                # app/events/transport.py) both tried to claim this
                # (event_id, handler_name) pair for the first time. The other
                # writer won; back off rather than run the handler twice.
                # Under Redis Streams in production this path is defensive
                # only — XREADGROUP already hands a given pending message to
                # one consumer at a time.
                await session.rollback()
                if is_new_record:
                    logger.info(
                        "event_processing_claim_lost_to_concurrent_worker",
                        event_id=str(event_id),
                        handler=sub.handler_name,
                    )
                    return LEASE_HELD
                raise

            last_error: str | None = None
            for attempt in range(record.attempts, self.max_retries):
                record.attempts = attempt + 1
                record.last_attempt_at = datetime.now(timezone.utc)
                try:
                    await sub.handler(event)
                    record.status = ProcessingStatus.SUCCESS
                    record.last_error = None
                    record.processed_at = datetime.now(timezone.utc)
                    event.status = EventStatus.PROCESSED
                    await session.commit()
                    logger.info(
                        "event_processed",
                        event_id=str(event_id),
                        handler=sub.handler_name,
                        attempts=record.attempts,
                    )
                    return ProcessingStatus.SUCCESS
                except Exception as exc:  # noqa: BLE001 — deliberately broad: any handler failure retries
                    last_error = str(exc)
                    record.last_error = last_error
                    event.retry_count = record.attempts
                    will_retry = attempt + 1 < self.max_retries
                    event.status = EventStatus.RETRYING if will_retry else EventStatus.FAILED
                    await session.commit()
                    logger.warning(
                        "event_handler_failed",
                        event_id=str(event_id),
                        handler=sub.handler_name,
                        attempt=record.attempts,
                        error=last_error,
                        will_retry=will_retry,
                    )
                    if self.retry_backoff_seconds:
                        await asyncio.sleep(self.retry_backoff_seconds * record.attempts)

            record.status = ProcessingStatus.DEAD_LETTER
            event.status = EventStatus.DEAD_LETTER
            session.add(
                DeadLetterEvent(
                    tenant_id=event.tenant_id,
                    event_id=event_id,
                    handler_name=sub.handler_name,
                    event_type=event.event_type,
                    reason=last_error or "unknown error",
                    payload=event.payload,
                )
            )
            await session.commit()
            logger.error(
                "event_dead_lettered",
                event_id=str(event_id),
                handler=sub.handler_name,
                reason=last_error,
            )
            return ProcessingStatus.DEAD_LETTER

    async def replay(self, event_id: uuid.UUID, handler_name: str) -> str:
        """Reset the processing record for (event_id, handler_name) and run the
        handler again immediately. Used to recover a dead-lettered event once
        its underlying cause (e.g. a downstream outage) is fixed.
        """
        async with self.session_factory() as session:
            event = await session.get(Event, event_id)
            if event is None:
                raise ValueError(f"Unknown event_id: {event_id}")
            await set_tenant_context(session, event.tenant_id)

            record = (
                await session.execute(
                    select(EventProcessingRecord).where(
                        EventProcessingRecord.event_id == event_id,
                        EventProcessingRecord.handler_name == handler_name,
                    )
                )
            ).scalar_one_or_none()
            if record is not None:
                record.attempts = 0
                record.status = ProcessingStatus.FAILED
                record.last_error = None
                record.last_attempt_at = None  # an explicit replay is not blocked by an old lease
            event.status = EventStatus.PUBLISHED
            await session.commit()

        subscriptions = self._subscriptions.get(event.event_type, [])
        sub = next((s for s in subscriptions if s.handler_name == handler_name), None)
        if sub is None:
            raise ValueError(f"No handler '{handler_name}' subscribed to {event.event_type}")

        logger.info("event_replay_started", event_id=str(event_id), handler=handler_name)
        return await self._handle_one(event_id, sub)

    async def reconcile_stuck_events(self, *, grace_seconds: float = 30.0, limit: int = 100) -> int:
        """Outbox-relay pass (Phase 12 production hardening): `publish()`
        durably writes the `Event` row to Postgres *before* enqueueing to
        the transport, by design — but the enqueue (`transport.send()`) is
        a separate step, not part of the same transaction (Redis can't
        join a Postgres transaction). If the process crashes, or the Redis
        call itself fails, between those two steps, the row exists and is
        genuinely durable, but nothing ever delivers it — `process_pending`
        only ever reads from the transport's consumer groups, never scans
        this table directly. That gap was found during the Phase 11
        production audit and is closed here: periodically (called from
        `EventWorker`'s tick loop, not on every tick) re-enqueue any
        `PUBLISHED` event older than `grace_seconds` whose event_type has
        subscribers. A redundant re-enqueue of an event that actually *was*
        delivered fine is always safe and cheap — the existing
        `EventProcessingRecord` per-(event_id, handler_name) uniqueness is
        exactly the same dedup guarantee that already protects against a
        genuinely duplicated transport delivery, so this never causes a
        handler to run twice.
        """
        # Phase 17B-2 note: this pass is a genuine, intentional multi-tenant
        # scan (any tenant's stuck PUBLISHED event, across the whole
        # table) — it is not a per-event tenant operation like _handle_one/
        # publish/replay above, so it deliberately does NOT call
        # set_tenant_context here. `events`/`event_processing_records` have
        # no RLS policy today (Phase 17A §4), so this is a documented,
        # harmless no-op for now; a real "system/global" DB identity for
        # operations exactly like this one is Phase 17B-3's explicit job,
        # not invented here.
        cutoff = datetime.now(timezone.utc).timestamp() - grace_seconds
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
        subscribed_types = self.subscribed_event_types()
        if not subscribed_types:
            return 0

        async with self.session_factory() as session:
            stuck = (
                await session.execute(
                    select(Event)
                    .where(
                        Event.status == EventStatus.PUBLISHED,
                        Event.event_type.in_(subscribed_types),
                        Event.created_at < cutoff_dt,
                    )
                    .limit(limit)
                )
            ).scalars().all()
            # Second kind of stuck event: a worker claimed it (record + PROCESSING committed) and died before finishing. Redis does not redeliver
            # a message that was read but not acked, and the PUBLISHED scan above ignores it, so without this pass the event stays PROCESSING
            # forever. An UNFINISHED record (neither SUCCESS nor DEAD_LETTER) whose lease has expired means exactly that. Re-sending is safe: the
            # delivery then takes the record over through the atomic lease claim in _handle_one, and a duplicate cannot run the handler twice.
            lease_cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.processing_lease_seconds)
            # (A subquery, not SELECT DISTINCT over `events`: PostgreSQL has no equality operator for the json `payload` column.)
            unfinished_event_ids = select(EventProcessingRecord.event_id).where(
                EventProcessingRecord.status == ProcessingStatus.FAILED,
                func.coalesce(EventProcessingRecord.last_attempt_at, EventProcessingRecord.created_at) < lease_cutoff,
            )
            abandoned = (
                await session.execute(
                    select(Event).where(Event.id.in_(unfinished_event_ids), Event.event_type.in_(subscribed_types)).limit(limit)
                )
            ).scalars().all()
            seen_ids: set[str] = set()
            events = []
            for e in [*stuck, *abandoned]:
                if str(e.id) in seen_ids:
                    continue
                seen_ids.add(str(e.id))
                events.append(
                    {
                        "event_id": str(e.id),
                        "tenant_id": str(e.tenant_id),
                        "event_type": e.event_type,
                        "correlation_id": str(e.correlation_id),
                    }
                )

        for fields in events:
            await self.transport.send(self._stream_name(fields["event_type"]), fields)
            logger.warning(
                "event_reconciled_after_stuck",
                event_id=fields["event_id"],
                event_type=fields["event_type"],
                grace_seconds=grace_seconds,
            )
        return len(events)
