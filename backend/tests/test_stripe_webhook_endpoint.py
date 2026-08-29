"""Phase 12C: Stripe webhook endpoint — signature validation, dedup, and
payment recording, fully testable without real Stripe credentials since
this test controls both the signer (matching Stripe's own documented
scheme) and the configured webhook secret."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.integration import WebhookEvent

pytestmark = pytest.mark.asyncio

_WEBHOOK_SECRET = "whsec_test_secret_for_webhook_endpoint_tests"


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


async def _make_invoice(session_factory, tenant_id: uuid.UUID) -> Invoice:
    async with session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Webhook Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id,
            customer_id=customer.id,
            invoice_number=f"WH-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED,
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            amount_due=Decimal("100.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(customer)
        return invoice, customer


async def test_invalid_signature_is_rejected_with_400(client) -> None:
    payload = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded"}).encode()
    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": "t=1,v1=deadbeef"}
    )
    assert resp.status_code == 400


async def test_missing_signature_header_is_rejected(client) -> None:
    payload = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded"}).encode()
    resp = await client.post("/api/v1/webhooks/stripe", content=payload)
    assert resp.status_code == 400


async def test_unhandled_event_type_is_accepted_and_recorded(client) -> None:
    payload = json.dumps({"id": f"evt_{uuid.uuid4().hex}", "type": "customer.created", "data": {"object": {}}}).encode()
    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


async def test_payment_intent_succeeded_records_a_real_payment(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, customer = await _make_invoice(async_session_maker, tenant_id)

    pi_id = f"pi_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": pi_id,
            "amount": 10000,
            "amount_received": 10000,
            "metadata": {
                "tenant_id": str(tenant_id),
                "invoice_id": str(invoice.id),
                "customer_id": str(customer.id),
            },
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        refreshed = await session.get(Invoice, invoice.id)
        assert refreshed.amount_paid == Decimal("100.00")
        assert refreshed.status == InvoiceStatus.PAID


async def test_duplicate_webhook_delivery_never_double_applies_a_payment(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, customer = await _make_invoice(async_session_maker, tenant_id)

    pi_id = f"pi_{uuid.uuid4().hex}"
    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": pi_id,
            "amount": 10000,
            "amount_received": 10000,
            "metadata": {
                "tenant_id": str(tenant_id),
                "invoice_id": str(invoice.id),
                "customer_id": str(customer.id),
            },
        }},
    }).encode()
    headers = {"Stripe-Signature": _sign(payload)}

    resp1 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "processed"

    resp2 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate_ignored"

    async with async_session_maker() as session:
        refreshed = await session.get(Invoice, invoice.id)
        # Still exactly 100.00 paid, not 200.00 — duplicate delivery must
        # never double-apply.
        assert refreshed.amount_paid == Decimal("100.00")


async def test_malformed_payload_missing_metadata_is_recorded_as_failed(client) -> None:
    from app.db.session import async_session_maker
    from sqlalchemy import select

    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_no_metadata", "amount": 5000}},
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
        assert row.error_detail is not None
