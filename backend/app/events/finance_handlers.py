"""Finance event subscribers — mirrors app/events/operations_handlers.py's
pattern. `invoice.trigger_requested` is the entry point into the invoice
lifecycle (job close-out publishes it — see app/services/completion_service.py);
this handler turns it into an idempotent DRAFT invoice. Other finance events
notify the customer via the *existing* CommunicationProvider — no second
messaging framework.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import MessageTemplate
from app.communications.factory import get_communication_provider
from app.events.bus import EventBus
from app.models.crm import Customer
from app.models.event import Event, EventType
from app.models.finance import Invoice
from app.services.invoice_service import InvoiceService

logger = structlog.get_logger(__name__)


def register_finance_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    invoice_service = InvoiceService(session_factory, bus)
    comms = get_communication_provider(session_factory)

    async def handle_invoice_trigger(event: Event) -> None:
        job_id = uuid.UUID(event.payload["job_id"]) if "job_id" in event.payload else event.entity_id
        invoice, deduped = await invoice_service.create_draft_from_job(event.tenant_id, job_id)
        logger.info("invoice_draft_from_trigger", invoice_id=str(invoice.id), job_id=str(job_id), deduped=deduped)

    async def notify_invoice_sent(event: Event) -> None:
        async with session_factory() as session:
            invoice = await session.get(Invoice, event.entity_id)
            if invoice is None:
                return
            customer = await session.get(Customer, invoice.customer_id)
        if not customer or not customer.email:
            return
        await comms.send_email(
            event.tenant_id, to=customer.email, subject=f"Invoice {invoice.invoice_number}",
            body=f"Invoice {invoice.invoice_number} for ${invoice.total} has been sent.",
            template=MessageTemplate.INVOICE_SENT,
        )

    async def notify_payment_received(event: Event) -> None:
        payment_id = event.payload.get("payment_id")
        logger.info("payment_received_notification", payment_id=payment_id)

    for event_type in (EventType.INVOICE_TRIGGER_REQUESTED,):
        bus.subscribe(event_type, "finance_invoice_trigger_handler", handle_invoice_trigger)

    for event_type in (EventType.INVOICE_SENT,):
        bus.subscribe(event_type, "finance_invoice_sent_notifier", notify_invoice_sent)

    for event_type in (EventType.PAYMENT_RECEIVED,):
        bus.subscribe(event_type, "finance_payment_received_notifier", notify_payment_received)
