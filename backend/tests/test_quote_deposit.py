"""Phase 15: quote acceptance + deposit collection — closes the loop
Quote created -> sent -> customer accepts -> deposit determined -> real
Stripe Checkout -> webhook -> Payment recorded -> quote deposit-paid ->
Job created. Covers quote state machine, Decimal-safe deposit math, the
Stripe checkout-session boundary (mocked at the httpx transport level,
same pattern as test_stripe_client.py), the real webhook endpoint
(signed, same pattern as test_stripe_payment_attribution_flow.py),
Payment persistence/tenant isolation, and a full realistic E2E scenario.
"""

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import decode_quote_view_token
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.finance import Payment
from app.models.operations import Job
from app.models.quote import Quote
from app.models.rbac import Role
from app.services.quote_deposit_service import (
    DepositNotRequiredError,
    InvalidDepositStateError,
    QuoteDepositService,
)
from app.services.quote_service import InvalidQuoteTransitionError, QuoteService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_WEBHOOK_SECRET = "whsec_test_secret_for_deposit_tests"
_ITEMS = [{"description": "Kitchen remodel", "quantity": "1", "unit_price": "1000.00"}]


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_customer(tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext) -> str:
    result = await tool_registry.execute(
        "crm.create_customer", {"name": "Deposit Test Customer", "email": "deposit-customer@example.com"}, ctx
    )
    return result.customer["id"]


async def _create_and_send_quote(
    tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext, *, deposit_type: str | None = None,
    deposit_value: str | None = None,
) -> tuple[str, str]:
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    body: dict = {"customer_id": customer_id, "line_items": _ITEMS}
    if deposit_type is not None:
        body["deposit_type"] = deposit_type
        body["deposit_value"] = deposit_value
    created = await tool_registry.execute("quotes.create_quote_draft", body, ctx)
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    token = sent.view_url_path.split("token=")[1]
    return created.quote["id"], token


def _sign(payload: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _deposit_success_payload(*, tenant_id: uuid.UUID, quote_id: str, customer_id: str, amount_cents: int) -> bytes:
    return json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": f"pi_{uuid.uuid4().hex}",
            "amount": amount_cents,
            "amount_received": amount_cents,
            "metadata": {
                "tenant_id": str(tenant_id), "quote_id": quote_id, "customer_id": customer_id,
                "purpose": "quote_deposit",
            },
        }},
    }).encode()


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


def _stripe_transport(checkout_session_id: str = "cs_test_123", url: str = "https://checkout.stripe.com/pay/cs_test_123"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, request=request,
            json={"id": checkout_session_id, "url": url, "payment_intent": "pi_fake"},
        )
    return httpx.MockTransport(handler)


def _patch_stripe_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    import app.integrations.stripe_client as mod

    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)


@pytest.fixture(autouse=True)
def _configure_stripe_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    yield


# --- 1. QUOTE: deposit configuration + acceptance state machine. ---


async def test_create_quote_with_percentage_deposit(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    result = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "PERCENTAGE", "deposit_value": "20"},
        ctx,
    )
    assert result.quote["deposit_type"] == "PERCENTAGE"
    assert result.quote["deposit_value"] == "20.00"
    # Not computed yet — deposit_amount is only frozen at accept time.
    assert result.quote["deposit_amount"] is None


async def test_percentage_deposit_over_100_is_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    with pytest.raises(ValueError, match="Percentage deposit"):
        await tool_registry.execute(
            "quotes.create_quote_draft",
            {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "PERCENTAGE", "deposit_value": "150"},
            ctx,
        )


async def test_fixed_deposit_exceeding_total_is_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    with pytest.raises(ValueError, match="cannot exceed"):
        await tool_registry.execute(
            "quotes.create_quote_draft",
            {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "5000.00"},
            ctx,
        )


async def test_zero_deposit_value_is_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    with pytest.raises(ValueError, match="must be > 0"):
        await tool_registry.execute(
            "quotes.create_quote_draft",
            {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "0"},
            ctx,
        )


async def test_no_deposit_quote_accept_behaves_exactly_as_before_phase_15(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["quote"]["status"] == "CONVERTED"
    assert body["job_created"] is True


async def test_deposit_quote_accept_holds_at_deposit_pending_no_job_yet(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="PERCENTAGE", deposit_value="20"
    )

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["quote"]["status"] == "DEPOSIT_PENDING"
    assert body["job_created"] is False
    assert body["quote"]["deposit_amount"] == "200.00"

    async with async_session_maker() as session:
        job_count = (
            await session.execute(select(func.count()).select_from(Job).where(Job.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        assert job_count == 0


async def test_deposit_pending_quote_cannot_be_accepted_again(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="100.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp.status_code == 409


async def test_deposit_quote_can_still_be_declined(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="PERCENTAGE", deposit_value="10"
    )
    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/decline", params={"token": token}, json={})
    assert resp.status_code == 200
    assert resp.json()["quote"]["status"] == "DECLINED"


# --- 2. DEPOSIT: Decimal-safe computation. ---


async def test_deposit_percentage_rounding_is_decimal_safe(event_bus) -> None:
    tenant_id = uuid.uuid4()
    service = QuoteService(async_session_maker, event_bus)
    from app.models.crm import Customer
    from app.services.invoice_service import LineItemInput

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Rounding Customer")
        session.add(customer)
        await session.flush()
        customer_id = customer.id
        await session.commit()

    quote, _ = await service.create_draft(
        tenant_id, customer_id=customer_id, lead_id=None,
        items=[LineItemInput(description="x", quantity=1, unit_price=Decimal("99.99"))],
        deposit_type="PERCENTAGE", deposit_value=Decimal("33.33"),
    )
    assert quote.total == Decimal("99.99")

    from app.invoice_delivery.factory import get_invoice_delivery_provider
    sent = await service.send(tenant_id, quote.id, get_invoice_delivery_provider(async_session_maker), "https://x")
    result = await service.decide(tenant_id, sent.id, accepted=True)
    # 99.99 * 33.33% = 33.326667 -> quantized to cents
    assert result.quote.deposit_amount == Decimal("33.33")
    assert isinstance(result.quote.deposit_amount, Decimal)


async def test_deposit_amount_immutable_after_accept_even_if_config_unchanged(event_bus) -> None:
    """deposit_amount is frozen at accept time and never recomputed by a
    second decide() call — the transition guard (status must be SENT/
    VIEWED) already makes a second decide() impossible, which IS the
    immutability guarantee; this proves it directly."""
    tenant_id = uuid.uuid4()
    service = QuoteService(async_session_maker, event_bus)
    from app.models.crm import Customer
    from app.services.invoice_service import LineItemInput

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Immutable Deposit Customer")
        session.add(customer)
        await session.flush()
        customer_id = customer.id
        await session.commit()

    quote, _ = await service.create_draft(
        tenant_id, customer_id=customer_id, lead_id=None,
        items=[LineItemInput(description="x", quantity=1, unit_price=Decimal("500.00"))],
        deposit_type="FIXED", deposit_value=Decimal("50.00"),
    )
    from app.invoice_delivery.factory import get_invoice_delivery_provider
    sent = await service.send(tenant_id, quote.id, get_invoice_delivery_provider(async_session_maker), "https://x")
    result = await service.decide(tenant_id, sent.id, accepted=True)
    assert result.quote.deposit_amount == Decimal("50.00")

    with pytest.raises(InvalidQuoteTransitionError):
        await service.decide(tenant_id, sent.id, accepted=True)

    async with async_session_maker() as session:
        row = await session.get(Quote, sent.id)
        assert row.deposit_amount == Decimal("50.00")


# --- 3. STRIPE: deposit checkout-session creation. ---


async def test_deposit_checkout_requires_deposit_pending_state(tool_registry, event_bus, monkeypatch) -> None:
    _patch_stripe_transport(monkeypatch, _stripe_transport())
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft",
        {"customer_id": customer_id, "line_items": _ITEMS, "deposit_type": "FIXED", "deposit_value": "100.00"},
        ctx,
    )
    from app.services.integration_connection_service import IntegrationConnectionService

    deposit_service = QuoteDepositService(async_session_maker, IntegrationConnectionService(async_session_maker))
    with pytest.raises(InvalidDepositStateError):
        await deposit_service.create_deposit_checkout_session(
            tenant_id, uuid.UUID(created.quote["id"]), success_url="https://x/success", cancel_url="https://x/cancel",
        )


async def test_deposit_checkout_requires_deposit_configured(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)
    from app.services.integration_connection_service import IntegrationConnectionService

    deposit_service = QuoteDepositService(async_session_maker, IntegrationConnectionService(async_session_maker))
    with pytest.raises(DepositNotRequiredError):
        await deposit_service.create_deposit_checkout_session(
            tenant_id, uuid.UUID(quote_id), success_url="https://x/success", cancel_url="https://x/cancel",
        )


async def test_deposit_checkout_without_stripe_key_is_honest_error(monkeypatch, tool_registry, client) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="50.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert resp.status_code == 503


async def test_deposit_checkout_session_via_public_endpoint(monkeypatch, tool_registry, client) -> None:
    _patch_stripe_transport(monkeypatch, _stripe_transport(url="https://checkout.stripe.com/pay/cs_deposit_abc"))
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="75.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert resp.status_code == 200
    assert resp.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_deposit_abc"


async def test_deposit_checkout_idempotency_key_is_stable_for_same_quote_and_amount(monkeypatch, tool_registry, client) -> None:
    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get("Idempotency-Key", ""))
        return httpx.Response(200, request=request, json={"id": "cs_1", "url": "https://checkout.stripe.com/pay/cs_1"})

    _patch_stripe_transport(monkeypatch, httpx.MockTransport(handler))
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="60.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert len(seen_keys) == 2
    assert seen_keys[0] == seen_keys[1]
    assert f"klaros-quote-deposit-{quote_id}-60.00" == seen_keys[0]


# --- 4. WEBHOOK: quote-deposit payment_intent.succeeded / duplicate delivery. ---


async def test_webhook_deposit_success_records_payment_and_converts_quote(client, tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="PERCENTAGE", deposit_value="20"
    )
    accept_resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert accept_resp.json()["quote"]["status"] == "DEPOSIT_PENDING"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = str(quote.customer_id)
        deposit_amount = quote.deposit_amount

    payload = _deposit_success_payload(
        tenant_id=tenant_id, quote_id=quote_id, customer_id=customer_id,
        amount_cents=int(deposit_amount * 100),
    )
    resp = await client.post("/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.status == "CONVERTED"
        assert quote.job_id is not None

        payment = (
            await session.execute(select(Payment).where(Payment.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        assert payment.amount == deposit_amount
        assert payment.provider == "stripe"
        assert payment.tenant_id == tenant_id

        job = await session.get(Job, quote.job_id)
        assert job is not None
        assert job.tenant_id == tenant_id


async def test_duplicate_deposit_webhook_never_double_records_or_double_converts(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="40.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = str(quote.customer_id)

    payload = _deposit_success_payload(tenant_id=tenant_id, quote_id=quote_id, customer_id=customer_id, amount_cents=4000)
    headers = {"Stripe-Signature": _sign(payload)}

    resp1 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp1.json()["status"] == "processed"
    resp2 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp2.json()["status"] == "duplicate_ignored"

    async with async_session_maker() as session:
        payment_count = (
            await session.execute(
                select(func.count()).select_from(Payment).where(Payment.quote_id == uuid.UUID(quote_id))
            )
        ).scalar_one()
        assert payment_count == 1
        job_count = (
            await session.execute(select(func.count()).select_from(Job).where(Job.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        assert job_count == 1


async def test_deposit_webhook_missing_quote_id_metadata_is_recorded_failed(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": f"pi_{uuid.uuid4().hex}", "amount": 5000, "amount_received": 5000,
            "metadata": {"tenant_id": str(tenant_id), "customer_id": str(uuid.uuid4()), "purpose": "quote_deposit"},
        }},
    }).encode()
    resp = await client.post("/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    from app.models.integration import WebhookEvent, WebhookProcessingStatus
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(WebhookEvent).where(WebhookEvent.tenant_id == tenant_id, WebhookEvent.provider == "stripe")
            )
        ).scalar_one()
        assert row.status == WebhookProcessingStatus.FAILED
        assert "quote_id" in row.error_detail


async def test_invoice_payment_webhook_path_still_unaffected_by_deposit_branch(client) -> None:
    """Regression: an ordinary invoice PaymentIntent (no purpose metadata)
    must still take the pre-Phase-15 code path unchanged."""
    from datetime import date
    from app.models.crm import Customer
    from app.models.finance import Invoice, InvoiceStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Invoice Path Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"REG-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("120.00"), total=Decimal("120.00"), amount_due=Decimal("120.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(customer)

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": f"pi_{uuid.uuid4().hex}", "amount": 12000, "amount_received": 12000,
            "metadata": {"tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id)},
        }},
    }).encode()
    resp = await client.post("/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)})
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        payment = (
            await session.execute(select(Payment).where(Payment.customer_id == customer.id))
        ).scalar_one()
        assert payment.quote_id is None
        assert payment.amount == Decimal("120.00")


# --- 5. PAYMENT: persistence, tenant isolation, quote linkage. ---


async def test_deposit_payment_is_tenant_isolated(client, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_a, ctx_a, deposit_type="FIXED", deposit_value="30.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = str(quote.customer_id)

    # A forged webhook claiming tenant_b's id for tenant_a's quote_id —
    # record_payment's own tenant-scoped Quote lookup inside
    # mark_deposit_paid must reject this rather than silently crossing
    # tenants (Quote.get scoped by tenant_id == tenant_b finds nothing).
    payload = _deposit_success_payload(tenant_id=tenant_b, quote_id=quote_id, customer_id=customer_id, amount_cents=3000)
    resp = await client.post("/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.status == "DEPOSIT_PENDING"  # untouched by the cross-tenant attempt
        payment_count = (
            await session.execute(select(func.count()).select_from(Payment).where(Payment.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        # The webhook handler verifies quote.tenant_id == the metadata's
        # claimed tenant_id BEFORE recording any Payment — a forged/
        # mismatched tenant_id never creates an orphan Payment row at all.
        assert payment_count == 0


# --- 6. RBAC on the staff-facing deposit tools. ---


async def test_technician_cannot_create_deposit_checkout_session(tool_registry, monkeypatch) -> None:
    from app.tools.errors import ToolError

    _patch_stripe_transport(monkeypatch, _stripe_transport())
    tenant_id = uuid.uuid4()
    owner_ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, owner_ctx, deposit_type="FIXED", deposit_value="20.00"
    )
    tech_ctx = _ctx(tenant_id, role=Role.TECHNICIAN)
    with pytest.raises(ToolError):
        await tool_registry.execute(
            "finance.create_quote_deposit_checkout_session",
            {"quote_id": quote_id, "success_url": "https://x/success", "cancel_url": "https://x/cancel"},
            tech_ctx,
        )


async def test_technician_cannot_read_deposit_status(tool_registry) -> None:
    from app.tools.errors import ToolError

    tenant_id = uuid.uuid4()
    owner_ctx = _ctx(tenant_id)
    quote_id, _token = await _create_and_send_quote(
        tool_registry, tenant_id, owner_ctx, deposit_type="FIXED", deposit_value="20.00"
    )
    tech_ctx = _ctx(tenant_id, role=Role.TECHNICIAN)
    with pytest.raises(ToolError):
        await tool_registry.execute("finance.get_quote_deposit_status", {"quote_id": quote_id}, tech_ctx)


async def test_owner_can_generate_deposit_checkout_via_tool(client, tool_registry, monkeypatch) -> None:
    _patch_stripe_transport(monkeypatch, _stripe_transport(url="https://checkout.stripe.com/pay/cs_staff"))
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="20.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    result = await tool_registry.execute(
        "finance.create_quote_deposit_checkout_session",
        {"quote_id": quote_id, "success_url": "https://x/success", "cancel_url": "https://x/cancel"},
        ctx,
    )
    assert result.checkout_url == "https://checkout.stripe.com/pay/cs_staff"


async def test_owner_can_read_deposit_status_via_tool(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, _token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="20.00"
    )
    result = await tool_registry.execute("finance.get_quote_deposit_status", {"quote_id": quote_id}, ctx)
    assert result.deposit_required is True
    assert result.deposit_type == "FIXED"
    assert result.status == "SENT"


# --- 7. END-TO-END. ---


async def test_full_deposit_collection_end_to_end(client, tool_registry, monkeypatch) -> None:
    _patch_stripe_transport(monkeypatch, _stripe_transport(url="https://checkout.stripe.com/pay/cs_e2e"))
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="PERCENTAGE", deposit_value="15"
    )

    accept_resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert accept_resp.json()["quote"]["status"] == "DEPOSIT_PENDING"
    assert accept_resp.json()["quote"]["deposit_amount"] == "150.00"

    checkout_resp = await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert checkout_resp.status_code == 200
    assert checkout_resp.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_e2e"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = str(quote.customer_id)

    payload = _deposit_success_payload(tenant_id=tenant_id, quote_id=quote_id, customer_id=customer_id, amount_cents=15000)
    webhook_resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert webhook_resp.json()["status"] == "processed"

    view_resp = await client.get(f"/api/v1/public/quotes/{quote_id}", params={"token": token})
    assert view_resp.json()["status"] == "CONVERTED"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.status == "CONVERTED"
        job = await session.get(Job, quote.job_id)
        assert job is not None
        payment = (
            await session.execute(select(Payment).where(Payment.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        assert payment.amount == Decimal("150.00")
        assert payment.external_id is not None
