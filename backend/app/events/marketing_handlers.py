"""Marketing event subscribers — the Marketing Attribution Loop itself.
Mirrors app/events/operations_handlers.py's / finance_handlers.py's pattern.
Consumes the *existing* lead/appointment/job/invoice/payment events and
advances the matching `CampaignConversion` row in place, only when that
lead is actually attributed to a campaign — no attribution is invented for
leads with no `CampaignLead` row.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.crm import Appointment
from app.models.event import Event, EventType
from app.models.finance import Invoice
from app.services.attribution_service import AttributionService

logger = structlog.get_logger(__name__)


def register_marketing_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    attribution = AttributionService(session_factory)

    async def handle_lead_qualified(event: Event) -> None:
        if event.entity_id is None:
            return
        await attribution.mark_qualified(event.tenant_id, event.entity_id)

    async def handle_appointment_created(event: Event) -> None:
        if event.entity_id is None:
            return
        async with session_factory() as session:
            appt = await session.get(Appointment, event.entity_id)
        if appt is None or appt.lead_id is None:
            return
        await attribution.mark_booked(event.tenant_id, appt.lead_id)

    async def handle_job_created(event: Event) -> None:
        job_id = event.entity_id
        lead_id = await attribution.lead_id_for_job(event.tenant_id, job_id)
        if lead_id is None:
            return
        from app.models.operations import Job

        async with session_factory() as session:
            job = await session.get(Job, job_id)
        if job is None:
            return
        await attribution.mark_job_created(event.tenant_id, lead_id, job_id, job.customer_id)

    async def handle_job_closed(event: Event) -> None:
        lead_id = await attribution.lead_id_for_job(event.tenant_id, event.entity_id)
        if lead_id is None:
            return
        await attribution.mark_job_closed(event.tenant_id, lead_id)

    async def handle_invoice_created(event: Event) -> None:
        invoice_id = event.entity_id
        lead_id = await attribution.lead_id_for_invoice(event.tenant_id, invoice_id)
        if lead_id is None:
            return
        async with session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
        if invoice is None:
            return
        await attribution.mark_invoiced(event.tenant_id, lead_id, invoice_id, invoice.total)

    async def handle_payment_received(event: Event) -> None:
        invoice_ids = event.payload.get("invoice_ids", [])
        for raw_id in invoice_ids:
            invoice_id = uuid.UUID(raw_id)
            lead_id = await attribution.lead_id_for_invoice(event.tenant_id, invoice_id)
            if lead_id is None:
                continue
            async with session_factory() as session:
                invoice = await session.get(Invoice, invoice_id)
            if invoice is None:
                continue
            await attribution.mark_paid(event.tenant_id, lead_id, invoice.amount_paid)

    bus.subscribe(EventType.LEAD_QUALIFIED, "marketing_attribution_lead_qualified", handle_lead_qualified)
    bus.subscribe(EventType.APPOINTMENT_CREATED, "marketing_attribution_appointment_created", handle_appointment_created)
    bus.subscribe(EventType.JOB_CREATED, "marketing_attribution_job_created", handle_job_created)
    bus.subscribe(EventType.JOB_CLOSED, "marketing_attribution_job_closed", handle_job_closed)
    bus.subscribe(EventType.INVOICE_CREATED, "marketing_attribution_invoice_created", handle_invoice_created)
    bus.subscribe(EventType.PAYMENT_RECEIVED, "marketing_attribution_payment_received", handle_payment_received)
