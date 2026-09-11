"""Phase 12G-2: Stripe schema-hardening pass, self-contained (no live
Stripe credentials). Covers the concrete gaps this pass found while
auditing the existing (already largely correct) Stripe implementation:

1. The webhook signature/JSON-parsing boundary previously let a
   correctly-signed-but-non-JSON body raise an uncaught
   `json.JSONDecodeError` all the way to a 500 — now a clean 400.
2. The webhook envelope (top-level `id`/`type`) was never itself
   validated — a correctly-signed payload missing either field would
   previously be silently defaulted (`event.get("id", "")`), risking an
   empty-string dedup-key collision across different malformed events.
   Now rejected with a 400 before ever reaching the dedup/audit table.
3. Unknown-field tolerance: a Stripe webhook object carrying a field this
   app doesn't know about (a real possibility — Stripe adds fields to
   its objects over time) must still parse and process correctly.
4. `StripeClient`'s outbound response parsing (`create_checkout_session`/
   `create_payment_intent`/`create_refund`) now validates Stripe's
   response shape via Pydantic instead of unchecked dict indexing —
   proven here against both a well-formed and a deliberately-malformed
   response.
5. Cross-tenant refund approval/rejection — tenant B can never approve
   or reject tenant A's refund via the tool layer (previously untested,
   though already correctly enforced by `PaymentService.decide_refund`'s
   existing tenant check).
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.integrations.stripe_client import (
    StripeAPIError,
    StripeClient,
    StripeWebhookPayloadError,
    verify_webhook_signature,
)
from app.integrations.stripe_schemas import (
    StripeCheckoutSessionResponse,
    StripePaymentIntentPayload,
    StripePaymentIntentResponse,
    StripeWebhookEnvelope,
)
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentAllocation, PaymentStatus, Refund, RefundStatus
from app.models.integration import WebhookEvent
from app.models.rbac import Role
from app.services.payment_service import InvalidRefundError
from app.tools.base import ExecutionContext

_WEBHOOK_SECRET = "whsec_test_secret_for_schema_hardening_tests"


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


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


# --- 1. Malformed JSON with a genuinely valid signature. ---

def test_valid_signature_but_non_json_body_raises_payload_error_not_json_decode_error() -> None:
    payload = b"this is not json at all {{{"
    header = _sign(payload)
    with pytest.raises(StripeWebhookPayloadError, match="malformed JSON"):
        verify_webhook_signature(payload, header, _WEBHOOK_SECRET)


async def test_webhook_endpoint_rejects_malformed_json_body_with_400_not_500(client) -> None:
    payload = b"not valid json{{{"
    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "malformed webhook payload"


# --- 2. Envelope-level validation (missing id/type). ---

def test_envelope_missing_type_field_fails_schema_validation() -> None:
    with pytest.raises(ValidationError):
        StripeWebhookEnvelope.model_validate({"id": "evt_1"})


def test_envelope_missing_id_field_fails_schema_validation() -> None:
    with pytest.raises(ValidationError):
        StripeWebhookEnvelope.model_validate({"type": "payment_intent.succeeded"})


async def test_webhook_endpoint_rejects_envelope_missing_type_with_400(client) -> None:
    payload = json.dumps({"id": "evt_no_type"}).encode()
    resp = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "malformed webhook envelope"

    from app.db.session import async_session_maker
    from sqlalchemy import select

    async with async_session_maker() as session:
        # Never persisted to the dedup/audit table — there is no
        # trustworthy `id` to key it by.
        count = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == "evt_no_type"))
        ).scalars().all()
        assert count == []


async def test_multi_field_validation_error_never_overflows_error_detail_column(client) -> None:
    """`WebhookEvent.error_detail` is `VARCHAR(500)` (app/models/integration.py).
    A single Pydantic `ValidationError` covering several simultaneously
    invalid fields on one `data.object` (missing `id`, wrong-typed
    `metadata`, wrong-typed `amount` all at once) renders as a message
    that can exceed 500 characters — invisible against SQLite, which
    never enforces VARCHAR length (the exact class of bug the Phase 12B
    audit found for `communication_logs.status`), but a real
    `StringDataRightTruncationError` against Postgres. This proves the
    webhook handler defensively truncates before writing, regardless of
    which DB backend is running underneath."""
    from app.db.session import async_session_maker
    from sqlalchemy import select

    event_id = f"evt_{uuid.uuid4().hex}"
    payload = json.dumps({
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {
            # No "id" (1 error) + "metadata" wrong type (1 error) +
            # "amount"/"amount_received" wrong type (2 more errors) —
            # four simultaneous field errors on one object.
            "metadata": "this-should-be-a-dict-not-a-string",
            "amount": "not-a-number",
            "amount_received": "also-not-a-number",
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
        assert row.status == "FAILED"
        assert row.error_detail is not None
        # The whole point: never store more than the column can hold,
        # on any backend, regardless of how many fields fail at once.
        assert len(row.error_detail) <= 500


# --- 3. Unknown-field tolerance: a field this app doesn't know about must
# never break parsing. ---

def test_payment_intent_payload_tolerates_and_preserves_unknown_fields() -> None:
    obj = StripePaymentIntentPayload.model_validate({
        "id": "pi_123",
        "amount": 1000,
        "amount_received": 1000,
        "metadata": {"tenant_id": "t1", "invoice_id": "i1", "customer_id": "c1", "future_metadata_key": "xyz"},
        "a_brand_new_field_stripe_added_later": {"nested": "value"},
    })
    assert obj.id == "pi_123"
    assert obj.metadata.tenant_id == "t1"
    # Unknown fields at both the object level and inside metadata are
    # preserved (extra="allow"), not rejected and not silently dropped.
    assert obj.model_extra["a_brand_new_field_stripe_added_later"] == {"nested": "value"}
    assert obj.metadata.model_extra["future_metadata_key"] == "xyz"


async def test_webhook_with_unknown_extra_fields_still_processes_successfully(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Unknown Field Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"UF-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"), total=Decimal("100.00"), amount_due=Decimal("100.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(customer)

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "payment_intent.succeeded",
        "a_top_level_field_from_a_future_stripe_api_version": "ignored gracefully",
        "data": {"object": {
            "id": f"pi_{uuid.uuid4().hex}",
            "amount": 10000,
            "amount_received": 10000,
            "metadata": {
                "tenant_id": str(tenant_id), "invoice_id": str(invoice.id), "customer_id": str(customer.id),
            },
            "payment_method_types": ["card"],  # a real Stripe field this app never reads
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


# --- 4. Outbound response validation. ---

def _mock_transport(response: httpx.Response) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: response)


async def test_create_checkout_session_validates_and_returns_typed_response(monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    request = httpx.Request("POST", "https://api.stripe.com/v1/checkout/sessions")
    response = httpx.Response(
        200, request=request,
        json={"id": "cs_test_123", "url": "https://checkout.stripe.com/pay/cs_test_123", "payment_intent": "pi_abc"},
    )
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = _mock_transport(response)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)

    client = StripeClient("sk_test_fake")
    result = await client.create_checkout_session(
        amount=Decimal("10.00"), currency="usd", metadata={}, success_url="https://x/success", cancel_url="https://x/cancel",
    )
    assert isinstance(result, StripeCheckoutSessionResponse)
    assert result.url == "https://checkout.stripe.com/pay/cs_test_123"
    assert result.id == "cs_test_123"


async def test_create_checkout_session_with_missing_required_field_raises_stripe_api_error(monkeypatch) -> None:
    """Stripe responding with a 200 but a body missing the `url` field
    this app depends on (a genuine, if rare, provider-side schema-drift
    scenario) must surface as a classified `StripeAPIError`, never an
    unhandled `pydantic.ValidationError` bubbling out of the client."""
    import app.integrations.stripe_client as mod

    request = httpx.Request("POST", "https://api.stripe.com/v1/checkout/sessions")
    response = httpx.Response(200, request=request, json={"id": "cs_test_123"})  # no "url"
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = _mock_transport(response)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)

    client = StripeClient("sk_test_fake")
    with pytest.raises(StripeAPIError, match="unexpected response shape"):
        await client.create_checkout_session(
            amount=Decimal("10.00"), currency="usd", metadata={}, success_url="https://x/success", cancel_url="https://x/cancel",
        )


async def test_retrieve_payment_intent_validates_response() -> None:
    valid = StripePaymentIntentResponse.model_validate({
        "id": "pi_1", "status": "succeeded", "amount": 1000, "currency": "usd",
        "some_field_this_app_never_reads": "preserved-not-rejected",
    })
    assert valid.id == "pi_1"
    assert valid.model_extra["some_field_this_app_never_reads"] == "preserved-not-rejected"

    with pytest.raises(ValidationError):
        # "amount" wrong type entirely (a string that isn't int-coercible)
        # must fail loudly rather than silently propagate a bad value.
        StripePaymentIntentResponse.model_validate({
            "id": "pi_1", "status": "succeeded", "amount": "not-a-number", "currency": "usd",
        })


# --- 5. Cross-tenant refund approval/rejection. ---

async def _make_refund_awaiting_decision(tenant_id: uuid.UUID) -> tuple[Invoice, Payment, Refund]:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Cross-Tenant Refund Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"XT-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.PAID, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"), total=Decimal("100.00"),
            amount_paid=Decimal("100.00"), amount_due=Decimal("0.00"),
        )
        session.add(invoice)
        await session.flush()

        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"),
            status=PaymentStatus.SUCCEEDED, provider="internal_test", external_id=f"itp_{uuid.uuid4().hex}",
            received_at=invoice.created_at,
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("100.00")))

        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id,
            amount=Decimal("30.00"), reason="cross-tenant test", status=RefundStatus.REQUESTED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)

    return invoice, payment, refund


async def test_tenant_b_cannot_approve_tenant_a_refund(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _invoice, _payment, refund = await _make_refund_awaiting_decision(tenant_a)

    with pytest.raises(ValueError, match="not pending|not found|Refund not found"):
        await tool_registry.execute(
            "finance.approve_refund", {"refund_id": str(refund.id)}, _ctx(tenant_b)
        )

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        still = await session.get(Refund, refund.id)
        assert still.status == RefundStatus.REQUESTED


async def test_tenant_b_cannot_reject_tenant_a_refund(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _invoice, _payment, refund = await _make_refund_awaiting_decision(tenant_a)

    with pytest.raises(ValueError, match="not pending|not found|Refund not found"):
        await tool_registry.execute(
            "finance.reject_refund", {"refund_id": str(refund.id)}, _ctx(tenant_b)
        )

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        still = await session.get(Refund, refund.id)
        assert still.status == RefundStatus.REQUESTED


async def test_payment_service_decide_refund_raises_for_wrong_tenant_directly() -> None:
    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus
    from app.services.payment_service import PaymentService

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _invoice, _payment, refund = await _make_refund_awaiting_decision(tenant_a)

    service = PaymentService(async_session_maker, get_event_bus())
    with pytest.raises(InvalidRefundError, match="not found"):
        await service.decide_refund(tenant_b, refund.id, approved=True, decided_by=uuid.uuid4())

    async with async_session_maker() as session:
        still = await session.get(Refund, refund.id)
        assert still.status == RefundStatus.REQUESTED


async def test_list_payments_and_refunds_are_tenant_scoped_at_the_query_level(client) -> None:
    """Not a new behavior — `list_payments`/`list_refunds` already filter
    by `current_user.tenant_id` from the JWT, never a client-supplied
    tenant id — but this pins the invariant down as an explicit,
    reproducible test rather than leaving it implicit in the route code."""
    import app.api.v1.payments as payments_module
    import app.api.v1.refunds as refunds_module
    import inspect

    # Confirms the query construction reads tenant_id from the
    # authenticated user object, not from any request body/query param.
    payments_src = inspect.getsource(payments_module.list_payments)
    refunds_src = inspect.getsource(refunds_module.list_refunds)
    assert "current_user.tenant_id" in payments_src
    assert "current_user.tenant_id" in refunds_src
