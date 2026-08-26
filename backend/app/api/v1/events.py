import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import get_wired_event_bus
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.event import Event

router = APIRouter(prefix="/events", tags=["events"])


class PublishEventRequest(BaseModel):
    event_type: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    payload: dict[str, Any] = {}
    idempotency_key: str | None = None


class EventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    status: str
    retry_count: int
    correlation_id: uuid.UUID
    deduplicated: bool = False

    model_config = {"from_attributes": True}


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def publish_event(
    payload: PublishEventRequest,
    current_user: CurrentUser = Depends(get_current_user),
    bus: EventBus = Depends(get_wired_event_bus),
) -> EventResponse:
    event = await bus.publish(
        tenant_id=current_user.tenant_id,
        event_type=payload.event_type,
        source="api",
        payload=payload.payload,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        idempotency_key=payload.idempotency_key,
    )
    resp = EventResponse.model_validate(event)
    resp.deduplicated = getattr(event, "was_deduplicated", False)
    return resp


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> EventResponse:
    async with async_session_maker() as session:
        event = await session.get(Event, event_id)
        if event is None or event.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        return EventResponse.model_validate(event)


@router.post("/process/{event_type}")
async def process_pending_events(
    event_type: str,
    current_user: CurrentUser = Depends(get_current_user),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, Any]:
    """Dev/ops endpoint: drive the subscriber loop on demand instead of
    waiting for a background worker. In production this runs continuously in
    a worker process, not on the request path.
    """
    del current_user
    stats = await bus.process_pending(event_type)
    return {
        "read": stats.read,
        "succeeded": stats.succeeded,
        "failed_retrying": stats.failed_retrying,
        "dead_lettered": stats.dead_lettered,
        "duplicates_skipped": stats.duplicates_skipped,
    }
