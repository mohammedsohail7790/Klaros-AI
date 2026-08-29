import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import DeadLetterEvent, Event, EventProcessingRecord
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


class PublishEventInput(BaseModel):
    event_type: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    payload: dict[str, Any] = {}
    idempotency_key: str | None = None


class PublishEventOutput(BaseModel):
    event_id: str
    status: str
    deduplicated: bool


class PublishEvent(Tool):
    name = "events.publish_event"
    description = "Publish a domain event onto the Klaros event bus."
    input_schema = PublishEventInput
    output_schema = PublishEventOutput

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def execute(self, input: PublishEventInput, context: ExecutionContext) -> PublishEventOutput:
        event = await self._bus.publish(
            tenant_id=context.tenant_id,
            event_type=input.event_type,
            source=f"tool:{context.actor_type}",
            payload=input.payload,
            entity_type=input.entity_type,
            entity_id=input.entity_id,
            correlation_id=context.correlation_id,
            idempotency_key=input.idempotency_key,
        )
        return PublishEventOutput(
            event_id=str(event.id),
            status=event.status,
            deduplicated=getattr(event, "was_deduplicated", False),
        )


class GetEventInput(BaseModel):
    event_id: uuid.UUID


class GetEventOutput(BaseModel):
    event_id: str
    event_type: str
    status: str
    retry_count: int
    payload: dict[str, Any]


class GetEvent(Tool):
    name = "events.get_event"
    description = "Fetch a single event by id, scoped to the caller's tenant."
    input_schema = GetEventInput
    output_schema = GetEventOutput

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetEventInput, context: ExecutionContext) -> GetEventOutput:
        async with self._session_factory() as session:
            event = await session.get(Event, input.event_id)
            # Tenant isolation: an event belonging to another tenant is treated
            # as not found, never leaked via a different error shape.
            if event is None or event.tenant_id != context.tenant_id:
                raise ValueError("Event not found")
            return GetEventOutput(
                event_id=str(event.id),
                event_type=event.event_type,
                status=event.status,
                retry_count=event.retry_count,
                payload=event.payload,
            )


class ListEventsInput(BaseModel):
    status: str | None = None
    event_type: str | None = None
    limit: int = 50


class EventSummary(BaseModel):
    event_id: str
    event_type: str
    status: str
    retry_count: int
    entity_type: str | None
    entity_id: str | None
    created_at: str


class ListEventsOutput(BaseModel):
    events: list[EventSummary]


class ListEvents(Tool):
    """Owner/operator visibility into the durable event store — the data
    behind the /events admin page. Read-only; never mutates."""

    name = "events.list_events"
    description = "List recent events for the tenant, optionally filtered by status/event_type."
    input_schema = ListEventsInput
    output_schema = ListEventsOutput
    required_permission = Permission.READ_EVENTS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: ListEventsInput, context: ExecutionContext) -> ListEventsOutput:
        async with self._session_factory() as session:
            query = select(Event).where(Event.tenant_id == context.tenant_id)
            if input.status:
                query = query.where(Event.status == input.status)
            if input.event_type:
                query = query.where(Event.event_type == input.event_type)
            query = query.order_by(Event.created_at.desc()).limit(min(input.limit, 200))
            rows = (await session.execute(query)).scalars().all()
            return ListEventsOutput(
                events=[
                    EventSummary(
                        event_id=str(e.id),
                        event_type=e.event_type,
                        status=e.status,
                        retry_count=e.retry_count,
                        entity_type=e.entity_type,
                        entity_id=str(e.entity_id) if e.entity_id else None,
                        created_at=e.created_at.isoformat(),
                    )
                    for e in rows
                ]
            )


class GetEventDetailInput(BaseModel):
    event_id: uuid.UUID


class ProcessingAttemptSummary(BaseModel):
    handler_name: str
    status: str
    attempts: int
    last_error: str | None
    last_attempt_at: str | None
    processed_at: str | None


class GetEventDetailOutput(BaseModel):
    event_id: str
    event_type: str
    status: str
    retry_count: int
    payload: dict[str, Any]
    created_at: str
    attempts: list[ProcessingAttemptSummary]


class GetEventDetail(Tool):
    name = "events.get_event_detail"
    description = "Fetch an event plus every handler's processing attempts, for the /events admin page."
    input_schema = GetEventDetailInput
    output_schema = GetEventDetailOutput
    required_permission = Permission.READ_EVENTS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetEventDetailInput, context: ExecutionContext) -> GetEventDetailOutput:
        async with self._session_factory() as session:
            event = await session.get(Event, input.event_id)
            if event is None or event.tenant_id != context.tenant_id:
                raise ValueError("Event not found")
            records = (
                await session.execute(
                    select(EventProcessingRecord).where(EventProcessingRecord.event_id == event.id)
                )
            ).scalars().all()
            return GetEventDetailOutput(
                event_id=str(event.id),
                event_type=event.event_type,
                status=event.status,
                retry_count=event.retry_count,
                payload=event.payload,
                created_at=event.created_at.isoformat(),
                attempts=[
                    ProcessingAttemptSummary(
                        handler_name=r.handler_name,
                        status=r.status,
                        attempts=r.attempts,
                        last_error=r.last_error,
                        last_attempt_at=r.last_attempt_at.isoformat() if r.last_attempt_at else None,
                        processed_at=r.processed_at.isoformat() if r.processed_at else None,
                    )
                    for r in records
                ],
            )


class ListDeadLettersInput(BaseModel):
    include_replayed: bool = False
    limit: int = 50


class DeadLetterSummary(BaseModel):
    dead_letter_id: str
    event_id: str
    event_type: str
    handler_name: str
    reason: str
    replayed: bool
    replayed_at: str | None
    created_at: str


class ListDeadLettersOutput(BaseModel):
    dead_letters: list[DeadLetterSummary]


class ListDeadLetters(Tool):
    name = "events.list_dead_letters"
    description = "List dead-lettered event/handler pairs for the tenant."
    input_schema = ListDeadLettersInput
    output_schema = ListDeadLettersOutput
    required_permission = Permission.READ_EVENTS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: ListDeadLettersInput, context: ExecutionContext) -> ListDeadLettersOutput:
        async with self._session_factory() as session:
            query = select(DeadLetterEvent).where(DeadLetterEvent.tenant_id == context.tenant_id)
            if not input.include_replayed:
                query = query.where(DeadLetterEvent.replayed.is_(False))
            query = query.order_by(DeadLetterEvent.created_at.desc()).limit(min(input.limit, 200))
            rows = (await session.execute(query)).scalars().all()
            return ListDeadLettersOutput(
                dead_letters=[
                    DeadLetterSummary(
                        dead_letter_id=str(d.id),
                        event_id=str(d.event_id),
                        event_type=d.event_type,
                        handler_name=d.handler_name,
                        reason=d.reason,
                        replayed=d.replayed,
                        replayed_at=d.replayed_at.isoformat() if d.replayed_at else None,
                        created_at=d.created_at.isoformat(),
                    )
                    for d in rows
                ]
            )


class ReplayDeadLetterInput(BaseModel):
    dead_letter_id: uuid.UUID


class ReplayDeadLetterOutput(BaseModel):
    dead_letter_id: str
    event_id: str
    handler_name: str
    result: str


class ReplayDeadLetter(Tool):
    """Manual recovery for a dead-lettered event/handler pair, once its root
    cause (a downstream bug, an outage) is believed fixed. Reuses the exact
    same idempotency-checked handler path as normal processing
    (`EventBus.replay` -> `EventBus._handle_one`) — a replay of an event
    whose handler somehow already succeeded (e.g. a second operator clicking
    retry) is a safe no-op, not a duplicate side effect."""

    name = "events.replay_dead_letter"
    description = "Retry a dead-lettered event/handler pair."
    input_schema = ReplayDeadLetterInput
    output_schema = ReplayDeadLetterOutput
    required_permission = Permission.MANAGE_EVENTS

    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def execute(self, input: ReplayDeadLetterInput, context: ExecutionContext) -> ReplayDeadLetterOutput:
        from datetime import datetime, timezone

        async with self._session_factory() as session:
            dead_letter = await session.get(DeadLetterEvent, input.dead_letter_id)
            if dead_letter is None or dead_letter.tenant_id != context.tenant_id:
                raise ValueError("Dead-lettered event not found")
            event_id = dead_letter.event_id
            handler_name = dead_letter.handler_name
            dead_letter.replayed = True
            dead_letter.replayed_at = datetime.now(timezone.utc)
            await session.commit()

        result = await self._bus.replay(event_id, handler_name)
        return ReplayDeadLetterOutput(
            dead_letter_id=str(input.dead_letter_id),
            event_id=str(event_id),
            handler_name=handler_name,
            result=result or "SKIPPED_ALREADY_SUCCEEDED",
        )


class GetEventWorkerMetricsInput(BaseModel):
    pass


class GetEventWorkerMetricsOutput(BaseModel):
    events_processed: int
    events_failed: int
    events_retried: int
    events_dead_lettered: int
    events_deduplicated: int
    ticks: int
    started_at: str | None
    last_tick_at: str | None
    last_tick_duration_ms: float | None
    per_event_type: dict[str, dict[str, int]]


class GetEventWorkerMetrics(Tool):
    """Real, in-process worker counters (see app/events/metrics.py) — never
    fabricated. Cross-tenant by nature (one worker process serves every
    tenant's events), so this intentionally does not filter by
    context.tenant_id; access is gated by MANAGE_EVENTS (an operator
    permission), not tenant membership."""

    name = "events.get_worker_metrics"
    description = "Return the running event worker's real processing counters."
    input_schema = GetEventWorkerMetricsInput
    output_schema = GetEventWorkerMetricsOutput
    required_permission = Permission.READ_EVENTS
    tenant_scoped = False

    async def execute(
        self, input: GetEventWorkerMetricsInput, context: ExecutionContext
    ) -> GetEventWorkerMetricsOutput:
        from app.events.metrics import worker_metrics

        snapshot = worker_metrics.snapshot()
        return GetEventWorkerMetricsOutput(**snapshot)
