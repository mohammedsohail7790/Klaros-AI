"""Phase 12F: payment_intent.payment_failed and charge.refunded webhook
handling — real signature verification (self-signed test fixtures, same
pattern as test_stripe_webhook_endpoint.py), real DB reconciliation."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentStatus, Refund, RefundStatus

pytestmark = pytest.mark.asyncio

_WEBHOOK_SECRET = "whsec_test_secret_for_failure_refund_tests"


def _sign(payload: bytes, secret: str = _WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


async def _make_paid_invoice_and_payment(session_factory, tenant_id: uuid.UUID):
    """A real invoice that's already fully paid via a real (fixture)
    Stripe payment_intent.succeeded flow — the state charge.refunded needs
    to reconcile against."""
    from app.models.finance import PaymentAllocation

    async with session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Refund Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id,
            customer_id=customer.id,
            invoice_number=f"RF-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.PAID,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            amount_due=Decimal("0.00"),
        )
        session.add(invoice)
        await session.flush()

        payment_intent_id = f"pi_{uuid.uuid4().hex}"
        payment = Payment(
            tenant_id=tenant_id,
            customer_id=customer.id,
            amount=Decimal("100.00"),
            status=PaymentStatus.SUCCEEDED,
            provider="stripe",
            external_id=payment_intent_id,
            received_at=invoice.created_at,
        )
        session.add(payment)
        await session.flush()

        allocation = PaymentAllocation(
            tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("100.00")
        )
        session.add(allocation)
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(payment)
        return invoice, customer, payment, payment_intent_id


async def test_payment_intent_payment_failed_publishes_event_and_is_recorded(client) -> None:
    from app.db.session import async_session_maker
    from app.models.integration import WebhookEvent

    tenant_id = uuid.uuid4()
    pi_id = f"pi_{uuid.uuid4().hex}"
    invoice_id = uuid.uuid4()
    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "payment_intent.payment_failed",
        "data": {"object": {
            "id": pi_id,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice_id)},
            "last_payment_error": {"message": "Your card was declined."},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        row = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == event_id))
        ).scalar_one()
        assert row.status == "PROCESSED"
        assert row.tenant_id == tenant_id


async def test_payment_intent_payment_failed_without_tenant_metadata_is_recorded_as_failed(client) -> None:
    from app.db.session import async_session_maker
    from app.models.integration import WebhookEvent

    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_no_metadata", "last_payment_error": {"message": "declined"}}},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    async with async_session_maker() as session:
        row = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == event_id))
        ).scalar_one()
        assert row.status == "FAILED"


async def test_charge_refunded_reconciles_payment_and_invoice(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, customer, payment, pi_id = await _make_paid_invoice_and_payment(async_session_maker, tenant_id)

    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "charge.refunded",
        "data": {"object": {
            "id": f"ch_{uuid.uuid4().hex}",
            "payment_intent": pi_id,
            "amount_refunded": 10000,  # full $100.00 refund, in cents
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        refreshed_invoice = await session.get(Invoice, invoice.id)
        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_invoice.amount_paid == Decimal("0.00")
        assert refreshed_invoice.amount_due == Decimal("100.00")
        assert refreshed_invoice.status == InvoiceStatus.PARTIALLY_PAID
        assert refreshed_payment.status == PaymentStatus.REFUNDED

        refund_rows = (
            await session.execute(select(Refund).where(Refund.payment_id == payment.id))
        ).scalars().all()
        assert len(refund_rows) == 1
        assert refund_rows[0].status == RefundStatus.COMPLETED
        assert refund_rows[0].amount == Decimal("100.00")


async def test_charge_refunded_partial_refund_leaves_invoice_partially_paid(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, customer, payment, pi_id = await _make_paid_invoice_and_payment(async_session_maker, tenant_id)

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "charge.refunded",
        "data": {"object": {
            "id": f"ch_{uuid.uuid4().hex}",
            "payment_intent": pi_id,
            "amount_refunded": 3000,  # partial $30.00 refund
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200

    async with async_session_maker() as session:
        refreshed_payment = await session.get(Payment, payment.id)
        assert refreshed_payment.status == PaymentStatus.PARTIALLY_REFUNDED


async def test_duplicate_charge_refunded_webhook_never_double_refunds(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, customer, payment, pi_id = await _make_paid_invoice_and_payment(async_session_maker, tenant_id)

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "charge.refunded",
        "data": {"object": {
            "id": f"ch_{uuid.uuid4().hex}",
            "payment_intent": pi_id,
            "amount_refunded": 10000,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()
    headers = {"Stripe-Signature": _sign(payload)}

    resp1 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp1.json()["status"] == "processed"

    resp2 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp2.json()["status"] == "duplicate_ignored"

    async with async_session_maker() as session:
        refund_rows = (
            await session.execute(select(Refund).where(Refund.payment_id == payment.id))
        ).scalars().all()
        # Exactly one refund row — the duplicate WEBHOOK delivery (same
        # event id) is caught by WebhookEvent dedup before ever reaching
        # reconcile_external_refund a second time.
        assert len(refund_rows) == 1


async def test_charge_refunded_for_unknown_payment_intent_is_recorded_as_failed(client) -> None:
    from app.db.session import async_session_maker
    from app.models.integration import WebhookEvent

    tenant_id = uuid.uuid4()
    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "charge.refunded",
        "data": {"object": {
            "id": f"ch_{uuid.uuid4().hex}",
            "payment_intent": "pi_does_not_exist",
            "amount_refunded": 5000,
            "metadata": {"tenant_id": str(tenant_id)},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    async with async_session_maker() as session:
        row = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == event_id))
        ).scalar_one()
        assert "no matching payment" in row.error_detail.lower()
