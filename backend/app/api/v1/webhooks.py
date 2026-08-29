"""Phase 12C: inbound provider webhooks. No JWT auth here — the provider's
own signature is the only trust boundary. Never mutate business state from
a payload before its signature verifies; never trust `tenant_id` from
unauthenticated request data (only from server-side metadata this
application itself stamped when it created the upstream object — e.g. a
Stripe PaymentIntent's metadata.tenant_id, set by
finance.create_stripe_checkout_session).

Pipeline (every provider): authenticate (signature) -> deduplicate
(WebhookEvent unique constraint) -> normalize -> act -> audit.
"""

import uuid
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.tool_deps import get_wired_event_bus
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.integrations.stripe_client import StripeWebhookSignatureError, verify_webhook_signature
from app.integrations.twilio_client import TwilioWebhookSignatureError
from app.integrations.twilio_client import verify_webhook_signature as verify_twilio_signature
from app.models.communication import CommunicationLog
from app.models.event import EventType
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.services.payment_service import AllocationInput, PaymentService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, str]:
    settings = get_settings()
    if not settings.STRIPE_WEBHOOK_SECRET:
        # Never process an unverifiable webhook — no secret configured means
        # we cannot distinguish a real Stripe request from a forged one.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe webhooks not configured")

    raw_body = await request.body()
    try:
        event = verify_webhook_signature(raw_body, stripe_signature or "", settings.STRIPE_WEBHOOK_SECRET)
    except StripeWebhookSignatureError as exc:
        logger.warning("stripe_webhook_signature_rejected", error=str(exc))
        # Deliberately NOT persisted to WebhookEvent — the event id itself
        # is unverified at this point, so trusting it as a dedup key would
        # let a forged payload collide with (and poison) a future real one.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature") from exc

    external_event_id = event.get("id", "")
    event_type = event.get("type", "unknown")

    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "stripe", WebhookEvent.external_event_id == external_event_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("stripe_webhook_duplicate_ignored", event_id=external_event_id, event_type=event_type)
            return {"status": "duplicate_ignored"}

        webhook_row = WebhookEvent(
            provider="stripe",
            external_event_id=external_event_id,
            event_type=event_type,
            raw_payload=event,
            status=WebhookProcessingStatus.RECEIVED,
        )
        session.add(webhook_row)
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent delivery raced us to the same unique constraint —
            # the other request is handling it; this one is a genuine
            # duplicate, not an error.
            await session.rollback()
            return {"status": "duplicate_ignored"}

    tenant_id: uuid.UUID | None = None
    status_value = WebhookProcessingStatus.PROCESSED
    error_detail: str | None = None

    try:
        if event_type == "payment_intent.succeeded":
            tenant_id, error_detail = await _handle_payment_intent_succeeded(event, bus)
            if error_detail:
                status_value = WebhookProcessingStatus.FAILED
        elif event_type == "payment_intent.payment_failed":
            tenant_id, error_detail = await _handle_payment_intent_failed(event, bus)
            if error_detail:
                status_value = WebhookProcessingStatus.FAILED
        elif event_type == "charge.refunded":
            tenant_id, error_detail = await _handle_charge_refunded(event, bus)
            if error_detail:
                status_value = WebhookProcessingStatus.FAILED
        else:
            logger.info("stripe_webhook_ignored_event_type", event_type=event_type, event_id=external_event_id)
    except Exception as exc:  # noqa: BLE001 — a processing failure must still be recorded, not crash the endpoint
        status_value = WebhookProcessingStatus.FAILED
        error_detail = str(exc)
        logger.error("stripe_webhook_processing_failed", event_id=external_event_id, error=str(exc))

    async with async_session_maker() as session:
        row = await session.get(WebhookEvent, webhook_row.id)
        if row is not None:
            row.status = status_value
            row.tenant_id = tenant_id
            row.error_detail = error_detail
            await session.commit()

    # Only publish to the internal, tenant-scoped event bus once a real
    # tenant has actually been resolved — the WebhookEvent row above is the
    # complete audit trail regardless, including for unhandled event types
    # or resolution failures where no tenant is known yet.
    if tenant_id is not None:
        await bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.WEBHOOK_RECEIVED,
            source="stripe_webhook",
            payload={"provider": "stripe", "event_type": event_type, "external_event_id": external_event_id},
            idempotency_key=f"stripe-webhook-{external_event_id}",
        )

    return {"status": "processed" if status_value == WebhookProcessingStatus.PROCESSED else "failed"}


async def _handle_payment_intent_succeeded(event: dict, bus: EventBus) -> tuple[uuid.UUID | None, str | None]:
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {})
    payment_intent_id = obj.get("id")

    tenant_id_raw = metadata.get("tenant_id")
    invoice_id_raw = metadata.get("invoice_id")
    customer_id_raw = metadata.get("customer_id")
    amount_cents = obj.get("amount_received") or obj.get("amount")

    if not (tenant_id_raw and invoice_id_raw and customer_id_raw and amount_cents and payment_intent_id):
        return None, "missing required metadata (tenant_id/invoice_id/customer_id) or amount on PaymentIntent"

    try:
        tenant_id = uuid.UUID(tenant_id_raw)
        invoice_id = uuid.UUID(invoice_id_raw)
        customer_id = uuid.UUID(customer_id_raw)
    except ValueError as exc:
        return None, f"malformed UUID in PaymentIntent metadata: {exc}"

    amount = Decimal(amount_cents) / Decimal(100)

    payment_service = PaymentService(async_session_maker, bus)
    try:
        await payment_service.record_payment(
            tenant_id,
            customer_id=customer_id,
            amount=amount,
            provider="stripe",
            external_id=payment_intent_id,
            payment_method="card",
            allocations=[AllocationInput(invoice_id=invoice_id, amount=amount)],
        )
    except Exception as exc:  # noqa: BLE001 — reported to the caller as processing failure, not re-raised past the webhook boundary
        return tenant_id, f"record_payment failed: {exc}"

    return tenant_id, None


async def _handle_payment_intent_failed(event: dict, bus: EventBus) -> tuple[uuid.UUID | None, str | None]:
    """A failed PaymentIntent never becomes a `Payment` row (nothing
    succeeded — there is no money movement to represent as one); this
    only publishes the existing `PAYMENT_FAILED` event so anything already
    subscribed to it (notifications, exception handling) reacts, and
    records the attempt in the WebhookEvent audit trail either way."""
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {})
    tenant_id_raw = metadata.get("tenant_id")
    invoice_id_raw = metadata.get("invoice_id")

    if not tenant_id_raw:
        return None, "missing tenant_id metadata on failed PaymentIntent"
    try:
        tenant_id = uuid.UUID(tenant_id_raw)
    except ValueError as exc:
        return None, f"malformed tenant_id in PaymentIntent metadata: {exc}"

    last_error = (obj.get("last_payment_error") or {}).get("message", "unknown reason")

    await bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.PAYMENT_FAILED,
        source="stripe_webhook",
        entity_type="invoice",
        entity_id=uuid.UUID(invoice_id_raw) if invoice_id_raw else None,
        payload={"provider": "stripe", "payment_intent_id": obj.get("id"), "reason": last_error},
        idempotency_key=f"stripe-payment-failed-{obj.get('id')}",
    )
    return tenant_id, None


async def _handle_charge_refunded(event: dict, bus: EventBus) -> tuple[uuid.UUID | None, str | None]:
    """Reconciles a refund issued OUTSIDE Klaros (Stripe Dashboard, a
    dispute, etc.) against the matching Payment — see
    PaymentService.reconcile_external_refund for the idempotent,
    cumulative-total-based reconciliation logic. Never a Klaros-initiated
    refund path (that's finance.create_refund_request -> approval ->
    PaymentService.decide_refund, unchanged)."""
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {})
    tenant_id_raw = metadata.get("tenant_id")
    payment_intent_id = obj.get("payment_intent")
    amount_refunded_cents = obj.get("amount_refunded")

    if not (tenant_id_raw and payment_intent_id and amount_refunded_cents is not None):
        return None, "missing tenant_id metadata, payment_intent, or amount_refunded on Charge"
    try:
        tenant_id = uuid.UUID(tenant_id_raw)
    except ValueError as exc:
        return None, f"malformed tenant_id in Charge metadata: {exc}"

    total_refunded = Decimal(amount_refunded_cents) / Decimal(100)

    payment_service = PaymentService(async_session_maker, bus)
    try:
        _refund, error = await payment_service.reconcile_external_refund(
            tenant_id,
            provider="stripe",
            external_payment_id=payment_intent_id,
            total_amount_refunded=total_refunded,
            reason="Refunded via Stripe (reconciled from charge.refunded webhook)",
        )
    except Exception as exc:  # noqa: BLE001 — reported to the caller as processing failure, not re-raised past the webhook boundary
        return tenant_id, f"reconcile_external_refund failed: {exc}"

    return tenant_id, error


@router.post("/twilio/status", status_code=status.HTTP_200_OK)
async def twilio_status_webhook(
    request: Request,
    twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, str]:
    """Twilio's delivery-status callback for an SMS this app already sent
    (configured via the `StatusCallback` param on the original send — not
    yet wired into TwilioSMSAdapter.send_sms, since doing so requires a
    real, publicly-reachable callback URL this sandbox doesn't have; see
    INTEGRATIONS.md). Updates the matching CommunicationLog row by
    MessageSid (external_id) once one exists."""
    settings = get_settings()
    if not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Twilio webhooks not configured")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    # Twilio signs over the exact public URL it POSTed to — this app's own
    # view of that URL (scheme+host+path+query as received), not a
    # hardcoded value, so this works correctly behind any real reverse
    # proxy/load balancer that forwards Host/X-Forwarded-Proto correctly.
    full_url = str(request.url)

    try:
        verify_twilio_signature(full_url, params, twilio_signature or "", settings.TWILIO_AUTH_TOKEN)
    except TwilioWebhookSignatureError as exc:
        logger.warning("twilio_webhook_signature_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature") from exc

    message_sid = params.get("MessageSid", "")
    message_status = params.get("MessageStatus", "unknown")

    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "twilio", WebhookEvent.external_event_id == f"{message_sid}:{message_status}"
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"status": "duplicate_ignored"}

        webhook_row = WebhookEvent(
            provider="twilio",
            # Twilio re-POSTs the SAME MessageSid for each status
            # transition (queued -> sent -> delivered) — those are
            # genuinely different events, not duplicates, so the dedup key
            # includes the status, not just the SID.
            external_event_id=f"{message_sid}:{message_status}",
            event_type="message_status_callback",
            raw_payload=params,
            status=WebhookProcessingStatus.RECEIVED,
        )
        session.add(webhook_row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"status": "duplicate_ignored"}

        log_row = (
            await session.execute(select(CommunicationLog).where(CommunicationLog.external_id == message_sid))
        ).scalar_one_or_none()
        tenant_id = log_row.tenant_id if log_row is not None else None
        if log_row is not None:
            log_row.status = f"TWILIO_{message_status.upper()}"[:40]

        webhook_row.tenant_id = tenant_id
        webhook_row.status = WebhookProcessingStatus.PROCESSED
        await session.commit()

    if tenant_id is not None:
        await bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.WEBHOOK_RECEIVED,
            source="twilio_webhook",
            payload={"provider": "twilio", "message_sid": message_sid, "message_status": message_status},
            idempotency_key=f"twilio-webhook-{message_sid}-{message_status}",
        )

    return {"status": "processed"}
