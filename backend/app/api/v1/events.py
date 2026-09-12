import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.api.tool_deps import execution_context, get_tool_registry, get_wired_event_bus, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.event import Event
from app.models.rbac import Permission
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

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


# --- Admin/operator routes (Phase 8). NOTE: literal paths must be declared
# before the generic "/{event_id}" route below, or FastAPI's path matching
# (by segment shape, not by declared type) would try to parse "dead-letters"
# or "metrics" as a UUID and 422 before ever reaching these handlers. ---


@router.get("")
async def list_events(
    status_filter: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "events.list_events",
            {"status": status_filter, "event_type": event_type, "limit": limit},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/dead-letters")
async def list_dead_letters(
    include_replayed: bool = False,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "events.list_dead_letters",
            {"include_replayed": include_replayed, "limit": limit},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/dead-letters/{dead_letter_id}/replay")
async def replay_dead_letter(
    dead_letter_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "events.replay_dead_letter",
            {"dead_letter_id": str(dead_letter_id)},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/metrics")
async def get_worker_metrics(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("events.get_worker_metrics", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/process/{event_type}")
async def process_pending_events(
    event_type: str,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, Any]:
    """Manual override, kept for operators — NOT required for normal
    operation as of Phase 8. The Klaros Event Worker (app/events/worker.py)
    now runs continuously (in-process when EVENT_TRANSPORT=memory, as its
    own `event-worker` Docker service against Redis in production) and
    drives this same `EventBus.process_pending` call on a poll loop, so
    published events propagate automatically. This endpoint still exists for
    forcing an immediate pass without waiting for the next poll tick.

    Gated behind MANAGE_AUTOMATIONS (the same permission
    dispatch_scheduled_tick in automations.py requires) — it forces
    processing of every tenant's pending events on this event_type's shared
    stream, not just the caller's own, since the underlying stream isn't
    partitioned per tenant. Without this gate any authenticated user of any
    tenant could trigger that global side effect for free.
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


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> EventResponse:
    async with async_session_maker() as session:
        event = await session.get(Event, event_id)
        if event is None or event.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        return EventResponse.model_validate(event)


@router.get("/{event_id}/detail")
async def get_event_detail(
    event_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "events.get_event_detail", {"event_id": str(event_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
