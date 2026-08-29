"""Phase 12F security-boundary gap tests for the Stripe integration.

Each test pins down a boundary that the earlier 11C-F test files established
indirectly but never asserted as an explicit invariant:

  1. No tool can ever record a `stripe`-sourced payment — only Stripe's
     signed webhook may mark Stripe money as received. `finance.record_test_payment`
     is the ONLY payment-recording tool and it ALWAYS uses the internal test
     provider, never `stripe`.
  2. A signed webhook whose metadata references ANOTHER tenant's invoice
     cannot credit that invoice; the webhook is recorded FAILED and no
     `Payment` row is created (PaymentService.record_payment raises
     InvoiceNotFoundError when the allocation's invoice is not the
     paying tenant's, and the whole transaction rolls back).
  3. finance.create_stripe_checkout_session refuses to run with no tenant
     connection and no platform STRIPE_SECRET_KEY — the missing-credential
     path must be a clean ToolError, and no Stripe HTTP call may occur.
  4. A Stripe API failure during checkout surfaces as a ToolError.
  5-7. finance.record_test_payment honors the tenant automation policy:
     APPROVAL_REQUIRED records an approval and records nothing;
     BLOCKED never executes; a genuinely APPROVED request (via
     ApprovalExecutionService.execute_approved) executes.
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.integrations.stripe_client import StripeAPIError, StripeClient, StripeErrorType
from app.models.actor import ActorType
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.models.integration import WebhookEvent
from app.services.approval_execution_service import ApprovalExecutionService
from app.services.policy_service import PolicyService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError, ToolBlockedError, ToolError
from app.tools.policy import ActionPolicy

pytestmark = pytest.mark.asyncio

_WEBHOOK_SECRET = "whsec_test_secret_for_phase12f_gap_tests"


def _sign(payload: bytes, secret: str = _WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _ctx(tenant_id, *, actor_id=None) -> ExecutionContext:
    from app.models.rbac import Role

    return ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id or uuid.uuid4(), role=Role.OWNER
    )


async def _make_customer_and_invoice(tool_registry, tenant_id: uuid.UUID, *, amount: str = "100.00") -> tuple[str, str]:
    ctx = _ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": f"Phase 12F {uuid.uuid4().hex[:6]}"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {
            "customer_id": customer.customer["id"],
            "line_items": [{"description": "Job", "quantity": "1", "unit_price": amount}],
        },
        ctx,
    )
    return customer.customer["id"], invoice.invoice["id"]


async def _make_invoice_row(session_factory, tenant_id: uuid.UUID) -> Invoice:
    async with session_factory() as session:
        from app.models.crm import Customer

        customer = Customer(tenant_id=tenant_id, name="Phase 12F Webhook Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id,
            customer_id=customer.id,
            invoice_number=f"P12F-{uuid.uuid4().hex[:8]}",
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


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    yield


# --- Invariant 1: no tool records Stripe money; only the webhook does. ---

async def test_no_tool_can_record_a_stripe_sourced_payment(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id, invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)

    payment = await tool_registry.execute(
        "finance.record_test_payment",
        {
            "customer_id": customer_id,
            "amount": "100.00",
            "allocations": [{"invoice_id": invoice_id, "amount": "100.00"}],
            "payment_method": "card",
        },
        ctx,
    )

    assert payment.payment["provider"] == "internal_test_payment"

    async with event_bus.session_factory() as session:
        rows = (await session.execute(select(Payment))).scalars().all()
        assert len(rows) == 1
        assert rows[0].provider == "internal_test_payment"
        # The `stripe` provider value is reserved exclusively for the
        # server-side signed-webhook path.
        assert rows[0].provider != "stripe"


# --- Invariant 2: a signed webhook cannot credit another tenant's invoice. ---

async def test_cross_tenant_webhook_metadata_cannot_credit_another_tenants_invoice(client) -> None:
    from app.db.session import async_session_maker

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _invoice_a, customer_a = await _make_invoice_row(async_session_maker, tenant_a)
    invoice_b, _customer_b = await _make_invoice_row(async_session_maker, tenant_b)

    # Attacker (or buggy redirect) stamps tenant_a's tenant_id in metadata but
    # targets tenant_b's invoice. tenant_a's key is verified — this is a
    # fully legit-signed payload — so the ONLY protection is tenant-scoped
    # validation inside record_payment.
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": f"pi_{uuid.uuid4().hex}",
            "amount": 10000,
            "amount_received": 10000,
            "metadata": {
                "tenant_id": str(tenant_a),
                "invoice_id": str(invoice_b.id),
                "customer_id": str(customer_a.id),
            },
        }},
    }).encode()

    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    async with async_session_maker() as session:
        event = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.provider == "stripe"))
        ).scalars().one()
        assert event.status == "FAILED"
        assert "record_payment failed" in (event.error_detail or "")
        assert event.tenant_id == tenant_a

        # Tenant B's invoice is completely untouched — no credit moved in
        # from tenant A's money.
        refreshed = await session.get(Invoice, invoice_b.id)
        assert refreshed.amount_paid == Decimal("0.00")
        assert refreshed.amount_due == Decimal("100.00")
        assert refreshed.status == InvoiceStatus.APPROVED

        # ...and no Payment exists at all: the failed record rolled back
        # entirely, so there is no orphaned `stripe` Payment row either.
        payment_count = (await session.execute(select(func.count()).select_from(Payment))).scalar_one()
        assert payment_count == 0


# --- Invariant 3: checkout refuses to run with no credential. ---

async def test_checkout_session_requires_credential(tool_registry, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", None)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    _customer_id, invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("StripeClient.create_checkout_session must not be called without a key")

    monkeypatch.setattr(StripeClient, "create_checkout_session", _fail_if_called)

    with pytest.raises(ToolError, match="Stripe is not connected"):
        await tool_registry.execute(
            "finance.create_stripe_checkout_session",
            {
                "invoice_id": invoice_id,
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
            ctx,
        )


# --- Invariant 4: a real Stripe API error surfaces as a ToolError. ---

async def test_checkout_session_surfaces_stripe_api_error(tool_registry, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_bogus_key_0123456789")

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id, invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)

    async def _raise_auth(*args, **kwargs):
        raise StripeAPIError("Invalid API Key provided", status_code=401, error_type=StripeErrorType.AUTHENTICATION)

    monkeypatch.setattr(StripeClient, "create_checkout_session", _raise_auth)

    with pytest.raises(ToolError, match="Stripe checkout session creation failed"):
        await tool_registry.execute(
            "finance.create_stripe_checkout_session",
            {
                "invoice_id": invoice_id,
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
                "customer_email": "owner@example.com",
            },
            ctx,
        )


# --- Invariants 5-7: payment tool honors the tenant automation policy. ---

async def test_approval_required_creates_no_payment(tool_registry, event_bus) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id, invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)

    await policy_service.set_policy(
        tenant_id, "finance.record_test_payment", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx.actor_id
    )

    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute(
            "finance.record_test_payment",
            {
                "customer_id": customer_id,
                "amount": "100.00",
                "allocations": [{"invoice_id": invoice_id, "amount": "100.00"}],
            },
            ctx,
        )

    async with event_bus.session_factory() as session:
        payment_count = (await session.execute(select(func.count()).select_from(Payment))).scalar_one()
        assert payment_count == 0

        invoice = await session.get(Invoice, uuid.UUID(invoice_id))
        assert invoice.amount_paid == Decimal("0.00")
        assert invoice.status == InvoiceStatus.DRAFT


async def test_blocked_never_executes(tool_registry, event_bus) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id, invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)

    await policy_service.set_policy(
        tenant_id, "finance.record_test_payment", ActionPolicy.BLOCKED, actor_id=ctx.actor_id
    )

    with pytest.raises(ToolBlockedError):
        await tool_registry.execute(
            "finance.record_test_payment",
            {
                "customer_id": customer_id,
                "amount": "100.00",
                "allocations": [{"invoice_id": invoice_id, "amount": "100.00"}],
            },
            ctx,
        )

    async with event_bus.session_factory() as session:
        payment_count = (await session.execute(select(func.count()).select_from(Payment))).scalar_one()
        assert payment_count == 0


async def test_approved_payment_executes_through_approval_service(tool_registry, event_bus) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id, invoice_id = await _make_customer_and_invoice(tool_registry, tenant_id)

    await policy_service.set_policy(
        tenant_id, "finance.record_test_payment", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx.actor_id
    )

    try:
        await tool_registry.execute(
            "finance.record_test_payment",
            {
                "customer_id": customer_id,
                "amount": "100.00",
                "allocations": [{"invoice_id": invoice_id, "amount": "100.00"}],
            },
            ctx,
        )
        assert False, "expected ToolApprovalRequiredError"
    except ToolApprovalRequiredError as exc:
        approval_id = exc.approval_request_id

    async with event_bus.session_factory() as session:
        request = await session.get(ApprovalRequest, approval_id)
        assert request.status == ApprovalStatus.PENDING
        request.status = ApprovalStatus.APPROVED
        await session.commit()

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, tool_registry._bus)
    result = await service.execute_approved(tenant_id, approval_id)
    assert result.execution_status == "EXECUTED"
    assert result.execution_error is None

    async with event_bus.session_factory() as session:
        rows = (await session.execute(select(Payment))).scalars().all()
        assert len(rows) == 1
        assert rows[0].provider == "internal_test_payment"
        assert rows[0].provider != "stripe"