"""Real-PostgreSQL concurrency verification for the Stripe webhook's
idempotency guarantee (`POST /webhooks/stripe`,
app/api/v1/webhooks.py:54-207). The existing Stripe webhook tests
(tests/test_stripe_webhook_endpoint.py,
tests/test_stripe_webhook_failure_and_refund.py) prove duplicate
delivery is handled SEQUENTIALLY (request 1, then request 2) — real
concurrent delivery (Stripe's own retry behavior can genuinely overlap
in-flight, and a naive multi-worker deployment could receive the same
redelivered event on two workers simultaneously) is a different code
path: the `try: session.add(...); await session.commit() / except
IntegrityError` guard in webhooks.py:139-146. SQLite's single-writer
serialization can never prove this branch is reachable/correct under
real concurrent transactions (see the Phase 28-30 precedent — the exact
same class of gap found real defects in Contract/Quote/AR sweeps). This
file exists to observe real behavior under genuine concurrent
PostgreSQL transactions, not to assume either safety or a defect.
"""

import asyncio
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
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.models.integration import WebhookEvent

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_WEBHOOK_SECRET = "whsec_test_secret_for_postgres_concurrency"


def _sign(payload: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


async def _make_invoice(session_factory, tenant_id: uuid.UUID) -> tuple[Invoice, Customer]:
    async with session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Concurrency Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id,
            customer_id=customer.id,
            invoice_number=f"WHPG-{uuid.uuid4().hex[:8]}",
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


@requires_real_postgres
async def test_genuinely_concurrent_duplicate_stripe_webhook_delivery_applies_payment_exactly_once(client) -> None:
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

    # 10 genuinely concurrent deliveries of the IDENTICAL event — real
    # asyncio.gather against the real running app + real PostgreSQL, not
    # sequential await calls.
    responses = await asyncio.gather(
        *[client.post("/api/v1/webhooks/stripe", content=payload, headers=headers) for _ in range(10)]
    )

    assert all(r.status_code == 200 for r in responses)
    statuses = [r.json()["status"] for r in responses]
    assert statuses.count("processed") == 1, f"expected exactly 1 'processed', got {statuses}"
    assert statuses.count("duplicate_ignored") == 9, f"expected exactly 9 'duplicate_ignored', got {statuses}"

    async with async_session_maker() as session:
        refreshed = await session.get(Invoice, invoice.id)
        assert refreshed.amount_paid == Decimal("100.00")  # never 200/300/... from a double-apply
        assert refreshed.status == InvoiceStatus.PAID

        payments = (
            await session.execute(select(Payment).where(Payment.tenant_id == tenant_id))
        ).scalars().all()
        assert len(payments) == 1

        webhook_rows = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "stripe", WebhookEvent.external_event_id == event_id
                )
            )
        ).scalars().all()
        assert len(webhook_rows) == 1  # the unique constraint held under real concurrent INSERTs


@requires_real_postgres
async def test_genuinely_concurrent_duplicate_charge_refunded_webhook_never_double_refunds(client) -> None:
    from app.db.session import async_session_maker
    from app.models.finance import Refund

    tenant_id = uuid.uuid4()
    invoice, customer = await _make_invoice(async_session_maker, tenant_id)

    pi_id = f"pi_{uuid.uuid4().hex}"
    charge_id = f"ch_{uuid.uuid4().hex}"
    paid_event = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": pi_id, "amount": 10000, "amount_received": 10000,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()
    r = await client.post("/api/v1/webhooks/stripe", content=paid_event, headers={"Stripe-Signature": _sign(paid_event)})
    assert r.status_code == 200 and r.json()["status"] == "processed"

    refund_event_id = f"evt_{uuid.uuid4().hex}"
    refund_payload = json.dumps({
        "id": refund_event_id,
        "type": "charge.refunded",
        "data": {"object": {
            "id": charge_id, "payment_intent": pi_id, "amount_refunded": 10000,
            "metadata": {"tenant_id": str(tenant_id)},
        }},
    }).encode()
    headers = {"Stripe-Signature": _sign(refund_payload)}

    responses = await asyncio.gather(
        *[client.post("/api/v1/webhooks/stripe", content=refund_payload, headers=headers) for _ in range(10)]
    )
    assert all(r.status_code == 200 for r in responses)
    statuses = [r.json()["status"] for r in responses]
    assert statuses.count("processed") == 1
    assert statuses.count("duplicate_ignored") == 9

    async with async_session_maker() as session:
        refunds = (await session.execute(select(Refund).where(Refund.tenant_id == tenant_id))).scalars().all()
        assert len(refunds) == 1
        assert refunds[0].amount == Decimal("100.00")  # never double-refunded
