"""Klaros's own platform-billing webhook (/api/v1/billing/webhook) —
signature validation, dedup, and the three subscription lifecycle events
it acts on. Mirrors tests/test_stripe_webhook_endpoint.py's shape for the
tenant-payments webhook, fully testable without real Stripe credentials."""

import hashlib
import hmac
import json
import time
import uuid

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.organization import Organization

pytestmark = pytest.mark.asyncio

_WEBHOOK_SECRET = "whsec_test_secret_for_billing_webhook_tests"


def _sign(payload: bytes, secret: str = _WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_PLATFORM_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


async def _make_org() -> Organization:
    async with async_session_maker() as session:
        org = Organization(
            name=f"Webhook Test Co {uuid.uuid4().hex[:6]}", slug=f"webhook-test-{uuid.uuid4().hex}", plan="growth",
            billing_status="trialing",
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


async def test_invalid_signature_is_rejected_with_400(client) -> None:
    payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
    resp = await client.post(
        "/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": "t=1,v1=deadbeef"}
    )
    assert resp.status_code == 400


async def test_checkout_session_completed_activates_the_subscription(client) -> None:
    org = await _make_org()
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": f"cs_{uuid.uuid4().hex}",
            "mode": "subscription",
            "customer": "cus_test123",
            "subscription": "sub_test123",
            "metadata": {"tenant_id": str(org.id), "plan": "solo"},
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        refreshed = await session.get(Organization, org.id)
        assert refreshed.billing_status == "active"
        assert refreshed.plan == "solo"
        assert refreshed.stripe_customer_id == "cus_test123"
        assert refreshed.stripe_subscription_id == "sub_test123"


async def test_subscription_deleted_marks_org_canceled(client) -> None:
    org = await _make_org()
    async with async_session_maker() as session:
        db_org = await session.get(Organization, org.id)
        db_org.stripe_subscription_id = "sub_to_cancel"
        db_org.billing_status = "active"
        await session.commit()

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_to_cancel", "customer": "cus_test123"}},
    }).encode()

    resp = await client.post(
        "/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200

    async with async_session_maker() as session:
        refreshed = await session.get(Organization, org.id)
        assert refreshed.billing_status == "canceled"


async def test_duplicate_webhook_delivery_is_ignored(client) -> None:
    org = await _make_org()
    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_dup", "mode": "subscription", "customer": "cus_dup", "subscription": "sub_dup",
            "metadata": {"tenant_id": str(org.id), "plan": "growth"},
        }},
    }).encode()
    headers = {"Stripe-Signature": _sign(payload)}

    resp1 = await client.post("/api/v1/billing/webhook", content=payload, headers=headers)
    assert resp1.json()["status"] == "processed"

    resp2 = await client.post("/api/v1/billing/webhook", content=payload, headers=headers)
    assert resp2.json()["status"] == "duplicate_ignored"
