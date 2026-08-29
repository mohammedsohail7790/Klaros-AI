"""The full Marketing Attribution Loop, run end to end against a real
(test-database) tenant:

    campaign -> spend -> lead -> attribution -> qualification -> appointment
    -> job -> close -> invoice -> payment -> attributed revenue -> campaign ROI

plus an incomplete-attribution scenario proving Klaros reports
"insufficient data" rather than inventing CAC/ROAS when spend or qualified
leads don't exist yet.
"""

import base64
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.event import EventType
from app.models.finance import Invoice
from app.models.marketing import CampaignConversion, ConversionStage
from app.models.rbac import Role
from app.services.attribution_service import AttributionService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_full_marketing_attribution_loop(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    campaign = await tool_registry.execute(
        "marketing.create_campaign",
        {"name": "Google Search - AC Repair", "channel": "GOOGLE_ADS", "objective": "LEAD_GEN", "total_budget": "1000.00"},
        ctx,
    )
    campaign_id = campaign.campaign["id"]

    await tool_registry.execute(
        "marketing.record_spend",
        {
            "channel": "GOOGLE_ADS", "amount": "200.00", "spend_date": date.today().isoformat(),
            "allocations": [{"campaign_id": campaign_id, "amount": "200.00"}],
        },
        ctx,
    )

    lead = await tool_registry.execute(
        "crm.create_lead",
        {
            "name": "Attribution Test Lead", "source": "REFERRAL", "service_requested": "AC repair",
            "location": "Austin, TX", "urgency": "EMERGENCY", "estimated_value": 800, "email": "lead@example.com",
        },
        ctx,
    )
    lead_id = lead.lead["id"]

    await tool_registry.execute(
        "marketing.attribute_lead",
        {"lead_id": lead_id, "campaign_id": campaign_id, "source": "google", "medium": "cpc", "attribution_model": "SOURCE_ONLY"},
        ctx,
    )

    qualify = await tool_registry.execute("crm.qualify_lead", {"lead_id": lead_id}, ctx)
    assert qualify.qualification_status == "QUALIFIED"
    await event_bus.process_pending(EventType.LEAD_QUALIFIED)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Attribution Test Lead", "email": "lead@example.com"}, ctx)
    customer_id = customer.customer["id"]

    appt = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer_id, "lead_id": lead_id, "title": "AC repair estimate",
            "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T10:00:00+00:00",
        },
        ctx,
    )
    await event_bus.process_pending(EventType.APPOINTMENT_CREATED)

    job_out = await tool_registry.execute(
        "operations.create_job",
        {
            "title": "AC repair", "customer_id": customer_id, "lead_id": lead_id,
            "estimated_revenue": 800.0, "estimated_cost": 300.0,
        },
        ctx,
    )
    job_id = job_out.job["id"]
    await event_bus.process_pending(EventType.JOB_CREATED)

    await tool_registry.execute(
        "operations.schedule_job",
        {"job_id": job_id, "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T11:00:00+00:00"}, ctx,
    )
    worker = await tool_registry.execute("operations.create_worker", {"name": "Sam", "service_types": ["HVAC"]}, ctx)
    await tool_registry.execute("operations.assign_job", {"job_id": job_id, "worker_id": worker.worker["id"]}, ctx)
    await tool_registry.execute("operations.dispatch_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"}, ctx)
    await tool_registry.execute("operations.start_job", {"job_id": job_id}, ctx)
    await tool_registry.execute(
        "operations.add_job_photo",
        {"job_id": job_id, "filename": "done.jpg", "content_type": "image/jpeg", "content_base64": base64.b64encode(b"photo bytes").decode()},
        ctx,
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, ctx)
    closed = await tool_registry.execute("operations.close_job", {"job_id": job_id}, ctx)
    assert closed.job["status"] == "CLOSED"
    await event_bus.process_pending(EventType.JOB_CLOSED)

    await event_bus.process_pending(EventType.INVOICE_TRIGGER_REQUESTED)
    async with event_bus.session_factory() as session:
        invoice = (
            await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id)))
        ).scalar_one()
    invoice_id = str(invoice.id)
    await event_bus.process_pending(EventType.INVOICE_CREATED)

    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)
    await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice_id}, ctx)
    await tool_registry.execute(
        "finance.record_test_payment",
        {"customer_id": customer_id, "amount": "800.00", "allocations": [{"invoice_id": invoice_id, "amount": "800.00"}]},
        ctx,
    )
    await event_bus.process_pending(EventType.PAYMENT_RECEIVED)

    attribution_service = AttributionService(event_bus.session_factory)
    perf = await attribution_service.campaign_performance(tenant_id, uuid.UUID(campaign_id))

    assert perf.spend == Decimal("200.00")
    assert perf.leads == 1
    assert perf.qualified_leads == 1
    assert perf.booked == 1
    assert perf.jobs_created == 1
    assert perf.jobs_closed == 1
    assert perf.invoiced_count == 1
    assert perf.revenue == Decimal("800.00")
    assert perf.collected_revenue == Decimal("800.00")
    assert perf.cac == Decimal("200.00")
    assert perf.roas == Decimal("4.00")

    async with event_bus.session_factory() as session:
        conversion = (
            await session.execute(
                select(CampaignConversion).where(
                    CampaignConversion.tenant_id == tenant_id, CampaignConversion.campaign_id == uuid.UUID(campaign_id),
                    CampaignConversion.lead_id == uuid.UUID(lead_id),
                )
            )
        ).scalar_one()
    assert conversion.stage == ConversionStage.PAID
    assert conversion.job_id == uuid.UUID(job_id)
    assert conversion.invoice_id == uuid.UUID(invoice_id)


async def test_incomplete_attribution_never_invents_cac_or_roas(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    campaign = await tool_registry.execute(
        "marketing.create_campaign", {"name": "No Conversions Yet", "channel": "META_ADS"}, ctx
    )
    campaign_id = campaign.campaign["id"]

    # Real spend, but zero leads/qualified leads attributed to this campaign.
    await tool_registry.execute(
        "marketing.record_spend",
        {
            "channel": "META_ADS", "amount": "150.00", "spend_date": date.today().isoformat(),
            "allocations": [{"campaign_id": campaign_id, "amount": "150.00"}],
        },
        ctx,
    )

    perf_output = await tool_registry.execute("marketing.get_campaign_performance", {"campaign_id": campaign_id}, ctx)
    assert perf_output.spend == "150.00"
    assert perf_output.leads == 0
    assert perf_output.qualified_leads == 0
    assert perf_output.cac is None
    assert perf_output.cac_note is not None and "Insufficient data" in perf_output.cac_note
    assert perf_output.roas is None
    assert perf_output.roas_note is not None

    # A campaign with real spend and zero qualified leads should also be
    # flagged as LOW_CONVERSION by the deterministic exception detector.
    flagged = await tool_registry.execute("marketing.detect_performance_exceptions", {}, ctx)
    assert campaign_id in flagged.flagged_campaign_ids

    from app.models.operations import ExceptionType, OperationsException

    async with event_bus.session_factory() as session:
        exc = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.type == ExceptionType.LOW_CONVERSION,
                    OperationsException.entity_id == uuid.UUID(campaign_id),
                )
            )
        ).scalar_one_or_none()
    assert exc is not None
