"""Operations event subscribers — mirrors app/events/crm_handlers.py's
pattern. Wires job status changes into the (internal test) communication
provider, real and tested (section 25/47).
"""

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.internal_test_adapter import InternalTestCommunicationAdapter
from app.events.bus import EventBus
from app.models.event import Event, EventType
from app.models.operations import Job
from app.services.operations_communication_service import OperationsCommunicationService

logger = structlog.get_logger(__name__)

_EVENT_TO_NOTIFIER = {
    EventType.JOB_SCHEDULED: "notify_job_scheduled",
    EventType.JOB_DISPATCHED: "notify_job_dispatched",
    EventType.JOB_EN_ROUTE: "notify_job_en_route",
    EventType.JOB_COMPLETED: "notify_job_completed",
}


def register_operations_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    comms = OperationsCommunicationService(session_factory, InternalTestCommunicationAdapter(session_factory))

    async def handle_job_communication(event: Event) -> None:
        method_name = _EVENT_TO_NOTIFIER[EventType(event.event_type)]
        async with session_factory() as session:
            job = await session.get(Job, event.entity_id)
        if job is None:
            return
        method = getattr(comms, method_name)
        sent = await method(event.tenant_id, job)
        logger.info("operations_communication_sent", job_id=str(job.id), event_type=event.event_type, sent=sent)

    for event_type in _EVENT_TO_NOTIFIER:
        bus.subscribe(event_type, "operations_communication_handler", handle_job_communication)
