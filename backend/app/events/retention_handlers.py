"""Retention & Referral event subscribers — mirrors
app/events/marketing_handlers.py's pattern. Consumes the *existing*
job/invoice/payment/lead/appointment events; never a second event bus.
Every handler is idempotent (checked via existing rows / unique
constraints), so duplicate delivery is safe.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.internal_test_adapter import InternalTestCommunicationAdapter
from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Appointment
from app.models.event import Event, EventType
from app.models.finance import Invoice
from app.models.operations import Job
from app.services.attribution_service import AttributionService
from app.services.campaign_service import CampaignService
from app.services.exception_service import ExceptionService
from app.services.lead_service import LeadService
from app.services.referral_service import ReferralService
from app.services.retention_service import RetentionService

logger = structlog.get_logger(__name__)


def register_retention_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    exception_service = ExceptionService(session_factory, bus)
    retention_service = RetentionService(session_factory, bus, exception_service)
    attribution_service = AttributionService(session_factory)
    campaign_service = CampaignService(session_factory, bus, exception_service)
    lead_service = LeadService(session_factory, bus)
    referral_service = ReferralService(session_factory, bus, campaign_service, attribution_service, lead_service)

    async def handle_job_closed(event: Event) -> None:
        if event.entity_id is None:
            return
        await retention_service.handle_job_closed(event.tenant_id, event.entity_id)

        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            job = await session.get(Job, event.entity_id)
        if job is None or job.lead_id is None:
            return
        await referral_service.mark_job(event.tenant_id, job.lead_id, job.id, job.customer_id)

    async def handle_lead_qualified(event: Event) -> None:
        if event.entity_id is None:
            return
        await referral_service.mark_qualified(event.tenant_id, event.entity_id)

    async def handle_appointment_created(event: Event) -> None:
        if event.entity_id is None:
            return
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            appt = await session.get(Appointment, event.entity_id)
        if appt is None or appt.lead_id is None:
            return
        await referral_service.mark_booked(event.tenant_id, appt.lead_id)

    async def handle_invoice_created(event: Event) -> None:
        if event.entity_id is None:
            return
        lead_id = await attribution_service.lead_id_for_invoice(event.tenant_id, event.entity_id)
        if lead_id is None:
            return
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            invoice = await session.get(Invoice, event.entity_id)
        if invoice is None:
            return
        await referral_service.mark_converted(event.tenant_id, lead_id, invoice.id, invoice.total)

    async def handle_payment_received(event: Event) -> None:
        invoice_ids = event.payload.get("invoice_ids", [])
        for raw_id in invoice_ids:
            invoice_id = uuid.UUID(raw_id)
            lead_id = await attribution_service.lead_id_for_invoice(event.tenant_id, invoice_id)
            if lead_id is None:
                continue
            async with session_factory() as session:
                await set_tenant_context(session, event.tenant_id)
                invoice = await session.get(Invoice, invoice_id)
            if invoice is None:
                continue
            await referral_service.mark_collected(event.tenant_id, lead_id, invoice.amount_paid)

    bus.subscribe(EventType.JOB_CLOSED, "retention_job_closed", handle_job_closed)
    bus.subscribe(EventType.LEAD_QUALIFIED, "retention_referral_lead_qualified", handle_lead_qualified)
    bus.subscribe(EventType.APPOINTMENT_CREATED, "retention_referral_appointment_created", handle_appointment_created)
    bus.subscribe(EventType.INVOICE_CREATED, "retention_referral_invoice_created", handle_invoice_created)
    bus.subscribe(EventType.PAYMENT_RECEIVED, "retention_referral_payment_received", handle_payment_received)
