"""Default event subscribers, registered at process startup.

`record_processed_audit_handler` is a minimal real handler — not a mock — used
to prove the end-to-end path (publish -> subscriber receives -> durable side
effect) works. Domain modules (Phase 3+) will add their own handlers here
(e.g. `lead.created` -> AI qualification) without touching the bus itself.
"""

import structlog

from app.events.bus import EventBus
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.event import Event, EventType

logger = structlog.get_logger(__name__)


def register_default_handlers(bus: EventBus) -> None:
    async def record_processed_audit_handler(event: Event) -> None:
        async with bus.session_factory() as session:
            session.add(
                AuditLog(
                    tenant_id=event.tenant_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    action="event.processed",
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    input_summary={"event_type": event.event_type},
                    result="success",
                    source_event_id=event.id,
                    correlation_id=event.correlation_id,
                )
            )
            await session.commit()
        logger.info("event_handler_ran", handler="audit_recorder", event_id=str(event.id))

    for event_type in EventType:
        bus.subscribe(event_type, "audit_recorder", record_processed_audit_handler)
