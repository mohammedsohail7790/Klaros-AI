"""Dropshipping store, branch 4: the Stripe webhook for a storefront order (`purpose == "dropshipping_order"`). Signed payloads, real endpoint, no Stripe calls."""

import hashlib
import hmac
import json
import time
import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.dropshipping import Order, SupplierOrder
from app.models.finance import Payment
from app.models.integration import WebhookEvent
from app.services.dropshipping_order_service import DropshippingOrderService
from tests.test_dropshipping_api import _h
from tests.test_dropshipping_orders_api import BASE
from tests.test_dropshipping_store_api import _checkout, _live_store, _order_row, stripe  # noqa: F401 - `stripe` is a fixture

SECRET = "whsec_test_secret_for_store_tests"
HOOK = "/api/v1/webhooks/stripe"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "STRIPE_WEBHOOK_SECRET", SECRET)


def _sign(payload: bytes) -> dict:
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}"}


def _event(kind: str, tenant_id, order_id, customer_id, cents: int, *, pi: str | None = None, event_id: str | None = None, currency: str = "usd", purpose: str = "dropshipping_order") -> bytes:
    metadata = {"tenant_id": str(tenant_id), "customer_id": str(customer_id), "purpose": purpose}
    if order_id is not None:
        metadata["order_id"] = str(order_id)
    return json.dumps({
        "id": event_id or f"evt_{uuid.uuid4().hex}", "type": kind,
        "data": {"object": {"id": pi or f"pi_{uuid.uuid4().hex}", "amount": cents, "amount_received": cents, "currency": currency, "metadata": metadata}},
    }).encode()


async def _post(client, payload: bytes):
    return await client.post(HOOK, content=payload, headers=_sign(payload))


async def _setup(client, monkeypatch, org: str, email: str):
    owner, tenant_id = await _live_store(client, monkeypatch, org, email)
    out = await _checkout(client)
    order = await _order_row(out["order_number"])
    return owner, tenant_id, order, out


async def _payments(pi: str | None = None) -> list[Payment]:
    async with async_session_maker() as session:
        stmt = select(Payment)
        if pi:
            stmt = stmt.where(Payment.external_id == pi)
        return list((await session.execute(stmt)).scalars())


async def _reload(order_id) -> Order:
    async with async_session_maker() as session:
        return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()


async def test_a_paid_event_pays_the_order_records_the_money_and_creates_the_supplier_work(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, out = await _setup(client, monkeypatch, "Webhook Pay Co", "webhook-pay@example.com")
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, pi="pi_store_1")
    resp = await _post(client, payload)
    assert resp.status_code == 200 and resp.json()["status"] == "processed"

    paid = await _reload(order.id)
    assert paid.status == "PAID" and paid.payment_reference == "pi_store_1" and paid.payment_source == "STRIPE" and paid.paid_at is not None
    pays = await _payments("pi_store_1")
    assert len(pays) == 1 and str(pays[0].amount) in ("35.00", "35.0") and pays[0].provider == "stripe" and pays[0].tenant_id == tenant_id
    async with async_session_maker() as session:
        sos = (await session.execute(select(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalars().all()
    assert len(sos) == 2 and {s.status for s in sos} == {"TO_PLACE"}                      # one per supplier: the work queue
    assert (await client.get(f"/api/v1/public/store/orders/{out['status_token']}")).json()["order"]["status"] == "PAID"


async def test_redelivery_and_a_second_event_for_the_same_payment_change_nothing(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, _ = await _setup(client, monkeypatch, "Dup Pay Co", "dup-pay@example.com")
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, pi="pi_dup", event_id="evt_dup_1")
    assert (await _post(client, payload)).json()["status"] == "processed"
    assert (await _post(client, payload)).json()["status"] == "duplicate_ignored"
    other_event = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, pi="pi_dup", event_id="evt_dup_2")
    assert (await _post(client, other_event)).status_code == 200
    assert len(await _payments("pi_dup")) == 1
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalar_one() == 2
    assert (await _reload(order.id)).status == "PAID"


async def test_a_wrong_amount_records_the_money_but_does_not_pay_the_order(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, _ = await _setup(client, monkeypatch, "Short Pay Co", "short-pay@example.com")
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 1000, pi="pi_short")
    resp = await _post(client, payload)
    assert resp.status_code == 200 and resp.json()["status"] == "failed"                    # permanent: flagged, not retried
    row = await _reload(order.id)
    assert row.status == "PENDING_PAYMENT" and "order total" in row.status_note and row.payment_reference == "pi_short"
    assert len(await _payments("pi_short")) == 1
    async with async_session_maker() as session:
        event = (await session.execute(select(WebhookEvent).order_by(WebhookEvent.created_at.desc()))).scalars().first()
    assert "does not match" in (event.error_detail or "")


async def test_a_payment_after_cancellation_is_recorded_and_flagged_for_refund(client, monkeypatch, stripe) -> None:
    owner, tenant_id, order, _ = await _setup(client, monkeypatch, "Late Pay Co", "late-pay@example.com")
    assert (await client.post(f"{BASE}/orders/{order.id}/cancel", json={"reason": "changed mind"}, headers=_h(owner))).status_code == 200
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, pi="pi_late")
    resp = await _post(client, payload)
    assert resp.status_code == 200 and resp.json()["status"] == "failed"
    row = await _reload(order.id)
    assert row.status == "CANCELLED" and "refund needed" in row.status_note.lower() and row.payment_reference == "pi_late"
    assert len(await _payments("pi_late")) == 1
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalar_one() == 0


async def test_an_order_that_is_not_the_tenants_is_rejected_and_no_payment_is_written(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, _ = await _setup(client, monkeypatch, "Wrong Tenant Co", "wrong-tenant@example.com")
    for oid, tid in ((uuid.uuid4(), tenant_id), (order.id, uuid.uuid4())):
        payload = _event("payment_intent.succeeded", tid, oid, order.customer_id, 3500, pi=f"pi_bad_{uuid.uuid4().hex[:6]}")
        resp = await _post(client, payload)
        assert resp.status_code == 200 and resp.json()["status"] == "failed"
    assert await _payments() == []
    assert (await _reload(order.id)).status == "PENDING_PAYMENT"


async def test_missing_or_malformed_order_id_fails_cleanly(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, _ = await _setup(client, monkeypatch, "Bad Meta Co", "bad-meta@example.com")
    for oid in (None, "not-a-uuid"):
        payload = _event("payment_intent.succeeded", tenant_id, oid, order.customer_id, 3500)
        resp = await _post(client, payload)
        assert resp.status_code == 200 and resp.json()["status"] == "failed"
    assert await _payments() == []


async def test_a_transient_failure_answers_502_and_the_redelivery_completes_the_order(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, _ = await _setup(client, monkeypatch, "Retry Pay Co", "retry-pay@example.com")
    original = DropshippingOrderService.mark_paid

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("database blip")

    monkeypatch.setattr(DropshippingOrderService, "mark_paid", _boom)
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, pi="pi_retry", event_id="evt_retry_1")
    assert (await _post(client, payload)).status_code == 502                                  # Stripe will redeliver
    assert (await _reload(order.id)).status == "PENDING_PAYMENT" and len(await _payments("pi_retry")) == 1
    monkeypatch.setattr(DropshippingOrderService, "mark_paid", original)
    assert (await _post(client, payload)).json()["status"] == "processed"                    # the same event, reprocessed
    assert (await _reload(order.id)).status == "PAID" and len(await _payments("pi_retry")) == 1


async def test_a_failed_card_leaves_the_order_unpaid_and_payable(client, monkeypatch, stripe) -> None:
    _, tenant_id, order, out = await _setup(client, monkeypatch, "Declined Co", "declined@example.com")
    payload = _event("payment_intent.payment_failed", tenant_id, order.id, order.customer_id, 3500)
    resp = await _post(client, payload)
    assert resp.status_code == 200 and resp.json()["status"] == "processed"
    assert (await _reload(order.id)).status == "PENDING_PAYMENT" and await _payments() == []
    assert (await client.post(f"/api/v1/public/store/orders/{out['status_token']}/pay")).status_code == 200


async def test_the_quote_deposit_and_invoice_paths_are_untouched(client, monkeypatch, stripe) -> None:
    """A PaymentIntent without our purpose still takes the old path (here: no invoice_id, so the old, unchanged refusal)."""
    _, tenant_id, order, _ = await _setup(client, monkeypatch, "Old Path Co", "old-path@example.com")
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, purpose="something_else")
    resp = await _post(client, payload)
    assert resp.status_code == 200 and resp.json()["status"] == "failed"
    assert (await _reload(order.id)).status == "PENDING_PAYMENT"
