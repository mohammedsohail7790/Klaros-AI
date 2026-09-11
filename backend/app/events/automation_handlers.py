"""Event trigger -> Automation Engine wiring (Rule 4/15). One generic
dispatcher subscribed to every real `EventType` — never a second event
transport, never a competing poller: this reuses the exact same
EventBus/EventWorker delivery, retry, and dead-letter machinery every
other domain handler in this codebase already relies on.

For each matching, ENABLED automation (tenant-scoped, `EVENT` trigger,
`trigger_config.event_type` equal to the delivered event's type), builds a
bounded, serializable context and calls `AutomationService.start_execution`
— which is itself idempotent on `(automation_version_id, source_event_id)`,
so an event redelivered by the worker's own retry logic can never start a
second execution.
"""

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.automation import Automation, AutomationStatus, AutomationVersion, TriggerType
from app.models.crm import Lead
from app.models.event import Event, EventType
from app.services.automation_service import AutomationService

logger = structlog.get_logger(__name__)


async def _build_event_context(session, event: Event) -> dict:
    """Bounded, serializable, tenant-scoped — never a raw ORM object.
    `lead` is specially enriched with a fresh DB load (the fields Rule 5's
    own examples reference — lead.score, lead.status — are usually NOT in
    a lightweight event payload); every other entity type falls back to
    the event's own real payload dict under its entity_type key, which is
    still real data the publishing domain service actually put there."""
    context: dict = {"event": {"type": event.event_type, "entity_type": event.entity_type, "entity_id": str(event.entity_id) if event.entity_id else None}}

    if event.entity_type == "lead" and event.entity_id:
        lead = await session.get(Lead, event.entity_id)
        if lead is not None:
            context["lead"] = {
                "id": str(lead.id), "name": lead.name, "status": lead.status,
                "qualification_status": lead.qualification_status, "score": lead.lead_score,
                "source": lead.source, "service_requested": lead.service_requested,
                "urgency": lead.urgency, "customer_id": str(lead.customer_id) if lead.customer_id else None,
            }
    elif event.entity_type:
        context[event.entity_type] = dict(event.payload or {})

    return context


def register_automation_handlers(bus: EventBus, session_factory: async_sessionmaker, ai_execution_service) -> None:
    service = AutomationService(session_factory, ai_execution_service)

    async def automation_dispatch_handler(event: Event) -> None:
        async with session_factory() as session:
            versions = (
                await session.execute(
                    select(AutomationVersion, Automation)
                    .join(Automation, Automation.published_version_id == AutomationVersion.id)
                    .where(
                        Automation.tenant_id == event.tenant_id,
                        Automation.status == AutomationStatus.ENABLED,
                        AutomationVersion.trigger_type == TriggerType.EVENT,
                    )
                )
            ).all()

        matches = [
            (version, automation) for version, automation in versions
            if version.trigger_config.get("event_type") == event.event_type
        ]
        if not matches:
            return

        async with session_factory() as session:
            context = await _build_event_context(session, event)

        for version, automation in matches:
            try:
                await service.start_execution(
                    event.tenant_id, automation, version, trigger_type=TriggerType.EVENT,
                    source_event_id=event.id, entity_type=event.entity_type, entity_id=event.entity_id,
                    context=context, triggered_by=None,
                )
            except Exception as exc:  # noqa: BLE001 — one automation's failure must never block others or the event worker
                logger.error(
                    "automation_dispatch_failed", automation_id=str(automation.id), event_id=str(event.id), error=str(exc),
                )

    for event_type in EventType:
        bus.subscribe(event_type, "automation_dispatch", automation_dispatch_handler)
