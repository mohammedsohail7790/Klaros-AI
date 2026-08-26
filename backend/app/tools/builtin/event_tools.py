import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import Event
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
