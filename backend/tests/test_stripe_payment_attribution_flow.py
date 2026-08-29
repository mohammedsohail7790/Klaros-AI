"""Phase 12F Step 15: a real Stripe payment (via the real webhook endpoint)
must flow into the SAME Marketing Attribution Loop already used by every
other payment source — no Stripe-specific attribution path, no duplicate
counting on a redelivered webhook. Exercises the full real chain: webhook
-> signature verify -> PaymentService.record_payment -> EventBus.publish
(PAYMENT_RECEIVED) -> marketing_handlers.handle_payment_received ->
AttributionService.mark_paid -> CampaignConversion -> campaign_performance()."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.models.crm import Customer, Lead
from app.models.finance import Invoice, InvoiceStatus
from app.models.marketing import Campaign, MarketingSpendAllocation
from app.models.operations import Job, JobStatus
from app.models.event import EventType
from app.services.attribution_service import AttributionService

pytestmark = pytest.mark.asyncio

_WEBHOOK_SECRET = "whsec_test_secret_for_attribution_flow_tests"


def _sign(payload: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


async def _build_attributed_lead_to_invoice(session_factory, tenant_id: uuid.UUID):
    """Real Campaign + spend + attributed Lead + Job (linked to the lead)
    + Invoice (linked to the job) — the exact chain campaign_performance()
    and the attribution handlers walk."""
    attribution = AttributionService(session_factory)

    async with session_factory() as session:
        campaign = Campaign(tenant_id=tenant_id, name="Spring HVAC Promo", channel="GOOGLE_ADS")
        session.add(campaign)
        await session.flush()

        session.add(MarketingSpendAllocation(tenant_id=tenant_id, spend_id=uuid.uuid4(), campaign_id=campaign.id, amount=Decimal("200.00")))

        lead = Lead(tenant_id=tenant_id, name="Attributed Lead", source="WEB", urgency="HIGH")
        session.add(lead)
        await session.flush()

        customer = Customer(tenant_id=tenant_id, name="Attributed Lead")
        session.add(customer)
        await session.flush()

        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, lead_id=lead.id,
            job_number=f"ATTR-{uuid.uuid4().hex[:6]}", title="HVAC repair", status=JobStatus.CLOSED,
        )
        session.add(job)
        await session.flush()

        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, job_id=job.id,
            invoice_number=f"ATTR-{uuid.uuid4().hex[:8]}", status=InvoiceStatus.APPROVED,
            issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("500.00"), total=Decimal("500.00"), amount_due=Decimal("500.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(campaign)
        await session.refresh(lead)
        await session.refresh(customer)
        await session.refresh(invoice)

    # Real attribution linkage, same as the marketing.attribute_lead tool,
    # plus the same mark_invoiced() call the real INVOICE_CREATED handler
    # makes (app/events/marketing_handlers.py::handle_invoice_created) —
    # not re-triggering that event here since this test's focus is the
    # payment->PAID step specifically, but revenue_amount must be real and
    # set for the roas assertion below to mean anything.
    await attribution.attribute_lead(tenant_id, lead.id, campaign_id=campaign.id, source="google", medium="cpc")
    await attribution.mark_qualified(tenant_id, lead.id)
    await attribution.mark_invoiced(tenant_id, lead.id, invoice.id, invoice.total)

    return campaign, lead, customer, invoice


async def test_real_stripe_payment_advances_attribution_to_paid_with_correct_revenue(client, event_bus) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    campaign, lead, customer, invoice = await _build_attributed_lead_to_invoice(async_session_maker, tenant_id)
    attribution = AttributionService(async_session_maker)

    pi_id = f"pi_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": pi_id,
            "amount": 50000,
            "amount_received": 50000,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    # PAYMENT_RECEIVED is durably published (outbox pattern) but only
    # dispatched to subscribers — including the marketing attribution
    # handler — when something processes the pending stream, exactly like
    # the real EventWorker does continuously in production.
    await event_bus.process_pending(EventType.PAYMENT_RECEIVED)

    performance = await attribution.campaign_performance(tenant_id, campaign.id)
    assert performance.collected_revenue == Decimal("500.00")
    assert performance.roas == Decimal("2.50")  # 500 revenue / 200 spend


async def test_duplicate_stripe_webhook_never_double_counts_attribution_revenue(client, event_bus) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    campaign, lead, customer, invoice = await _build_attributed_lead_to_invoice(async_session_maker, tenant_id)
    attribution = AttributionService(async_session_maker)

    pi_id = f"pi_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": pi_id,
            "amount": 50000,
            "amount_received": 50000,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()
    headers = {"Stripe-Signature": _sign(payload)}

    resp1 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp1.json()["status"] == "processed"
    resp2 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp2.json()["status"] == "duplicate_ignored"

    await event_bus.process_pending(EventType.PAYMENT_RECEIVED)

    performance = await attribution.campaign_performance(tenant_id, campaign.id)
    # Still exactly $500 collected, not $1000 — the duplicate delivery
    # never reached record_payment a second time, so PAYMENT_RECEIVED
    # never re-fired, so mark_paid never ran twice.
    assert performance.collected_revenue == Decimal("500.00")


async def test_unattributed_lead_payment_does_not_fabricate_attribution(client, event_bus) -> None:
    """A payment for an invoice whose lead has no campaign attribution
    must never invent a CampaignConversion row — the existing, real
    behavior (lead_id_for_invoice / attribute_lead's CampaignLead dedup)
    already guarantees this; this test proves it stays true through the
    real Stripe webhook path specifically."""
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.marketing import CampaignConversion

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="No Attribution Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id,
            invoice_number=f"NOATTR-{uuid.uuid4().hex[:8]}", status=InvoiceStatus.APPROVED,
            issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("75.00"), total=Decimal("75.00"), amount_due=Decimal("75.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(customer)

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": f"pi_{uuid.uuid4().hex}",
            "amount": 7500,
            "amount_received": 7500,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.json()["status"] == "processed"
    await event_bus.process_pending(EventType.PAYMENT_RECEIVED)

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(CampaignConversion).where(CampaignConversion.tenant_id == tenant_id))
        ).scalars().all()
        assert rows == []
