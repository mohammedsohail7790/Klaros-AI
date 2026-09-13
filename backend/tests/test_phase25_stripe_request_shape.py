"""Phase 25: closes a real test-coverage gap found while auditing the
real-provider adapter boundary (STEP 8) — every existing Stripe MockTransport
test either checks generic client behavior (retry/error classification,
`test_stripe_client.py`) or captures only the `Idempotency-Key` header
(`test_quote_deposit.py`, `test_phase22_stripe_hardening.py`). NONE of them
decode and assert the actual outgoing request body/method/path for the two
most safety-critical writes — Checkout Session creation (what a customer is
actually charged) and Refund creation (how much money comes back).

This file adds exactly those two targeted assertions and nothing else — it
does not duplicate any already-covered behavior."""

import uuid
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import get_settings
from app.integrations.stripe_client import StripeClient
from app.models.finance import Payment, PaymentStatus
from app.services.payment_service import PaymentService

from tests.test_quote_deposit import _WEBHOOK_SECRET, _ctx, _create_and_send_quote

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _configure_stripe(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


def _patch_stripe_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    import app.integrations.stripe_client as mod

    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)


async def test_deposit_checkout_session_request_shape_is_correct(client, tool_registry, monkeypatch) -> None:
    """The single most safety-critical outgoing Stripe request in this
    codebase — what a real customer is actually charged. Asserts method,
    path, auth, idempotency key, and every field of the decoded form body:
    amount (minor units), currency, metadata (tenant/quote/customer/purpose),
    and that success/cancel URLs are the server-built ones, never a
    client-supplied value."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth_header"] = request.headers.get("Authorization", "")
        captured["idempotency_key"] = request.headers.get("Idempotency-Key", "")
        captured["body"] = parse_qs(request.content.decode())
        return httpx.Response(
            200, request=request,
            json={"id": "cs_shape_test", "url": "https://checkout.stripe.com/pay/cs_shape_test", "payment_intent": "pi_shape_test"},
        )

    _patch_stripe_transport(monkeypatch, httpx.MockTransport(handler))

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="75.50"
    )
    accept_resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert accept_resp.status_code == 200

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert resp.status_code == 200

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/checkout/sessions"
    # httpx's `auth=(secret_key, "")` basic-auth tuple -> a real Authorization header, never the raw key alone.
    assert captured["auth_header"].startswith("Basic ")
    assert captured["idempotency_key"] == f"klaros-quote-deposit-{quote_id}-75.50"

    body = captured["body"]
    assert body["mode"] == ["payment"]
    # $75.50 -> 7550 cents, the real Stripe minor-unit convention.
    assert body["line_items[0][price_data][unit_amount]"] == ["7550"]
    assert body["line_items[0][price_data][currency]"] == ["usd"]
    assert body["line_items[0][quantity]"] == ["1"]
    assert body["metadata[tenant_id]"] == [str(tenant_id)]
    assert body["metadata[quote_id]"] == [quote_id]
    assert body["metadata[purpose]"] == ["quote_deposit"]
    assert "customer_id" in "".join(k for k in body if k.startswith("metadata[customer_id]"))
    # Server-built redirect URLs only -- never anything the client could have supplied
    # (this endpoint takes no success_url/cancel_url parameter at all).
    assert body["success_url"][0].startswith(get_settings().FRONTEND_BASE_URL)
    assert body["cancel_url"][0].startswith(get_settings().FRONTEND_BASE_URL)
    assert f"/quotes/view/{quote_id}" in body["success_url"][0]


async def test_refund_request_shape_is_correct(event_bus, monkeypatch) -> None:
    """The other safety-critical outgoing Stripe request: how much money
    actually comes back to the customer. Asserts method, path, idempotency
    key, and that the exact refund amount (minor units) and the correct
    PaymentIntent id are sent -- never anything else."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["idempotency_key"] = request.headers.get("Idempotency-Key", "")
        captured["body"] = parse_qs(request.content.decode())
        return httpx.Response(
            200, request=request,
            json={
                "id": "re_shape_test", "amount": 3000, "currency": "usd",
                "status": "succeeded", "payment_intent": "pi_shape_refund_test",
            },
        )

    _patch_stripe_transport(monkeypatch, httpx.MockTransport(handler))

    from app.db.session import async_session_maker
    from app.models.crm import Customer
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Refund Shape Test Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"),
            status=PaymentStatus.SUCCEEDED, provider="stripe", external_id="pi_shape_refund_test",
            received_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())
    refund = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("30.00"), reason="test", requested_by=None,
    )
    await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/refunds"
    assert captured["idempotency_key"] == f"klaros-refund-{refund.id}"

    body = captured["body"]
    assert body["payment_intent"] == ["pi_shape_refund_test"]
    # $30.00 -> 3000 cents.
    assert body["amount"] == ["3000"]
