"""section 23: the first real CRM automations, wired onto the Phase 2 event
bus so lead creation never blocks on qualification (section 6/24).

    lead.created -> LeadQualificationService.qualify() -> lead.qualified /
    lead.unqualified (published by the service itself)

    appointment.created -> a notification is created (booking flow, section 14)

A Temporal LeadQualificationWorkflow wrapping the same qualification call
also exists (app/workflows/definitions.py) per the spec's instruction to use
Temporal here — see PROJECT_STATUS.md for why the event-bus path, not the
Temporal path, is what's actually exercised by the test suite.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Appointment
from app.models.event import Event, EventType
from app.models.notification import Notification
from app.services.enrichment_service import LeadEnrichmentService
from app.services.qualification_service import LeadQualificationService

logger = structlog.get_logger(__name__)


def register_crm_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    enrichment = LeadEnrichmentService(session_factory)
    qualification_service = LeadQualificationService(session_factory, bus, enrichment)

    async def qualify_new_lead(event: Event) -> None:
        lead_id = event.entity_id or uuid.UUID(event.payload["lead_id"])
        await qualification_service.qualify(event.tenant_id, lead_id, preserve_decided=True)
        logger.info("lead_auto_qualified", lead_id=str(lead_id))

    async def notify_on_appointment_created(event: Event) -> None:
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            appointment = await session.get(Appointment, event.entity_id)
            if appointment is None:
                return
            session.add(
                Notification(
                    tenant_id=event.tenant_id,
                    title="New appointment booked",
                    body=f"'{appointment.title}' scheduled for {appointment.start_time.isoformat()}.",
                    severity="INFO",
                    category="crm",
                )
            )
            await session.commit()

    bus.subscribe(EventType.LEAD_CREATED, "lead_qualification_handler", qualify_new_lead)
    bus.subscribe(
        EventType.APPOINTMENT_CREATED, "appointment_notification_handler", notify_on_appointment_created
    )
