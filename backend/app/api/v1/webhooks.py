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
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.tool_deps import get_wired_event_bus
from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.integrations.stripe_client import (
    StripeWebhookPayloadError,
    StripeWebhookSignatureError,
    verify_webhook_signature,
)
from app.integrations.stripe_schemas import (
    StripeChargePayload,
    StripePaymentIntentPayload,
    StripeWebhookEnvelope,
)
from app.integrations.twilio_client import TwilioWebhookSignatureError
from app.integrations.twilio_client import verify_webhook_signature as verify_twilio_signature
from app.models.communication import CommunicationLog
from app.models.event import EventType
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.models.quote import Quote
from app.services.customer_matching import normalize_phone
from app.services.lead_service import CreateLeadInput, LeadService
from app.services.voice_call_service import VoiceCallService
from app.services.payment_service import AllocationInput, InvoiceNotFoundError, OverpaymentError, PaymentService
from app.services.quote_service import InvalidQuoteTransitionError, QuoteNotFoundError, QuoteService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# A failure with one of these prefixes means Stripe genuinely captured a
# payment (or we already told Stripe a quote deposit was paid) and Klaros
# failed to fully record/act on it — money moved but our own state is
# incomplete. Every write in this module is idempotency-key/unique-
# constraint safe against reprocessing (see the module docstring), so
# these are exactly the cases where Stripe's own webhook retry (triggered
# by a non-2xx response) should be allowed to happen rather than silently
# swallowed behind an always-200 response, which previously left a
# captured customer payment with no Payment row and no automatic
# redelivery path — recoverable only via a manual Stripe Dashboard resend.
# Anything else (malformed metadata, a UUID that doesn't parse, a quote
# that doesn't belong to the claimed tenant) is a permanent, not
# transient, failure — retrying won't fix a payload that can't change, so
# those stay a 200 to avoid Stripe retrying forever for no benefit.
_RETRYABLE_ERROR_PREFIXES = ("record_payment failed:", "mark_deposit_paid failed:")


@router.post("/stripe", status_code=status.HTTP_200_OK, response_model=None)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, str] | JSONResponse:
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
    except StripeWebhookPayloadError as exc:
        # The signature IS genuinely valid here (verify_webhook_signature
        # only raises this after the HMAC check passes) — this is a
        # different failure: the signed bytes aren't valid JSON. Also not
        # persisted, for the same reason as an invalid signature: there is
        # no trustworthy `id` to dedup against.
        logger.warning("stripe_webhook_payload_malformed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="malformed webhook payload") from exc

    try:
        envelope = StripeWebhookEnvelope.model_validate(event)
    except ValidationError as exc:
        # Well-formed JSON, genuinely signed, but missing the two fields
        # (`id`/`type`) every handler needs before it can do anything —
        # structurally malformed, not a business-data gap (that case is
        # handled per-event-type below: recorded FAILED, still HTTP 200).
        # Deliberately log only field locations/error types, never
        # `str(exc)`/`exc.errors()`'s `input` — Pydantic's default error
        # rendering echoes back a repr of the actual (possibly
        # PII-bearing) input value for every failed field, which has no
        # business being duplicated into structured logs beyond what the
        # signed request already legitimately carries.
        error_summary = [{"field": ".".join(str(p) for p in e["loc"]), "type": e["type"]} for e in exc.errors()]
        logger.warning("stripe_webhook_envelope_invalid", errors=error_summary)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="malformed webhook envelope") from exc

    external_event_id = envelope.id
    event_type = envelope.type

    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "stripe", WebhookEvent.external_event_id == external_event_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.status != WebhookProcessingStatus.FAILED:
            # A row already exists and it isn't stuck FAILED — either it
            # already processed successfully, or another request is
            # currently in the middle of processing it (RECEIVED) — a
            # genuine duplicate, never something to retry here.
            logger.info("stripe_webhook_duplicate_ignored", event_id=external_event_id, event_type=event_type)
            return {"status": "duplicate_ignored"}

        if existing is not None:
            # Phase 23 fix: the previous delivery of this SAME event
            # genuinely failed (e.g. a transient DB error mid-processing,
            # not a permanent data-shape problem) — a redelivery (Stripe's
            # own retry, or an operator manually resending it from the
            # Stripe Dashboard) must be able to actually reprocess it.
            # Before this fix, ANY existing row — including a FAILED one —
            # was treated as a permanent duplicate, so a transient failure
            # during, e.g., Job creation could leave a customer's real
            # deposit paid with no Job ever created and NO possible
            # recovery path, not even a manual webhook resend. Reuse the
            # existing row (external_event_id is still unique) rather than
            # inserting a second one for the same event.
            webhook_row = existing
            webhook_row.raw_payload = event
            webhook_row.status = WebhookProcessingStatus.RECEIVED
            await session.commit()
        else:
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
            tenant_id, error_detail = await _handle_payment_intent_succeeded(envelope, bus)
            if error_detail:
                status_value = WebhookProcessingStatus.FAILED
        elif event_type == "payment_intent.payment_failed":
            tenant_id, error_detail = await _handle_payment_intent_failed(envelope, bus)
            if error_detail:
                status_value = WebhookProcessingStatus.FAILED
        elif event_type == "charge.refunded":
            tenant_id, error_detail = await _handle_charge_refunded(envelope, bus)
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
            # `error_detail` is VARCHAR(500) (models/integration.py). A
            # Pydantic ValidationError with multiple simultaneous field
            # errors (e.g. several missing/malformed fields on one
            # PaymentIntent/Charge object at once) can legitimately produce
            # a message well past 500 characters — invisible against
            # SQLite (which never enforces VARCHAR length) but a real
            # `StringDataRightTruncationError` against Postgres, the same
            # class of bug found in Phase 12B. Truncate defensively rather
            # than let a malformed webhook's error message crash the
            # webhook endpoint's own audit write.
            row.error_detail = error_detail[:500] if error_detail else None
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

    if (
        status_value == WebhookProcessingStatus.FAILED
        and error_detail is not None
        and error_detail.startswith(_RETRYABLE_ERROR_PREFIXES)
    ):
        # Non-2xx so Stripe's own retry/redelivery mechanism kicks in —
        # everything this failure could retry into is idempotency-key or
        # unique-constraint safe (see module docstring), so a redelivery
        # can only complete the recording that failed here, never
        # double-record it.
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"status": "failed"})

    return {"status": "processed" if status_value == WebhookProcessingStatus.PROCESSED else "failed"}


async def _handle_payment_intent_succeeded(
    envelope: StripeWebhookEnvelope, bus: EventBus
) -> tuple[uuid.UUID | None, str | None]:
    try:
        obj = StripePaymentIntentPayload.model_validate(envelope.data.object)
    except ValidationError as exc:
        # Missing/wrong-typed `id` — the one field this schema requires.
        # Everything else (metadata, amount) is intentionally optional
        # here and validated for BUSINESS meaning just below, matching
        # this handler's pre-Pydantic behavior exactly.
        return None, f"malformed PaymentIntent payload: {exc}"

    payment_intent_id = obj.id
    tenant_id_raw = obj.metadata.tenant_id
    customer_id_raw = obj.metadata.customer_id
    amount_cents = obj.amount_received or obj.amount

    if not (tenant_id_raw and customer_id_raw and amount_cents and payment_intent_id):
        return None, "missing required metadata (tenant_id/customer_id) or amount on PaymentIntent"

    try:
        tenant_id = uuid.UUID(tenant_id_raw)
        customer_id = uuid.UUID(customer_id_raw)
    except ValueError as exc:
        return None, f"malformed UUID in PaymentIntent metadata: {exc}"

    amount = Decimal(amount_cents) / Decimal(100)

    if obj.metadata.purpose == "quote_deposit":
        return await _handle_quote_deposit_succeeded(
            tenant_id, customer_id=customer_id, amount=amount, payment_intent_id=payment_intent_id,
            quote_id_raw=obj.metadata.quote_id, bus=bus,
        )

    invoice_id_raw = obj.metadata.invoice_id
    if not invoice_id_raw:
        return None, "missing required metadata (invoice_id) on PaymentIntent"
    try:
        invoice_id = uuid.UUID(invoice_id_raw)
    except ValueError as exc:
        return None, f"malformed UUID in PaymentIntent metadata: {exc}"

    payment_service = PaymentService(async_session_maker, bus, get_integration_connection_service())
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
    except (InvoiceNotFoundError, OverpaymentError) as exc:
        # Permanent, not transient — the exact same payload will raise the
        # exact same rejection every time (e.g. metadata claims a tenant
        # that doesn't own this invoice, or the allocation exceeds what's
        # actually owed). Retrying via Stripe's own redelivery can never
        # fix a payload that can't change, so this deliberately does NOT
        # use the "record_payment failed:" prefix _RETRYABLE_ERROR_PREFIXES
        # matches on above.
        return tenant_id, f"record_payment rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — reported to the caller as processing failure, not re-raised past the webhook boundary
        return tenant_id, f"record_payment failed: {exc}"

    return tenant_id, None


async def _handle_quote_deposit_succeeded(
    tenant_id: uuid.UUID, *, customer_id: uuid.UUID, amount: Decimal, payment_intent_id: str,
    quote_id_raw: str | None, bus: EventBus,
) -> tuple[uuid.UUID | None, str | None]:
    """A quote-deposit PaymentIntent has no Invoice to allocate against
    (see app/services/payment_service.py::record_payment — allocations=[]
    is already a supported, no-op-safe case). The Payment is instead
    linked directly via `Payment.quote_id`, then the quote itself is
    advanced DEPOSIT_PENDING -> DEPOSIT_PAID -> CONVERTED (see
    QuoteService.mark_deposit_paid), which is what actually creates the
    downstream Job — mirroring the no-deposit accept flow, just gated on
    payment instead of on the accept click alone."""
    if not quote_id_raw:
        return tenant_id, "missing quote_id metadata on quote-deposit PaymentIntent"
    try:
        quote_id = uuid.UUID(quote_id_raw)
    except ValueError as exc:
        return tenant_id, f"malformed quote_id in PaymentIntent metadata: {exc}"

    # Verify the quote genuinely belongs to the claimed tenant BEFORE
    # recording any Payment — record_payment itself has no way to know
    # quote_id's real owner (Payment.quote_id isn't a validated FK at the
    # DB layer), so without this check a forged/mismatched tenant_id in
    # the metadata could create an orphan Payment row scoped to the wrong
    # tenant even though it's never linked to that tenant's own quote.
    async with async_session_maker() as session:
        quote = await session.get(Quote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        return tenant_id, f"quote {quote_id} does not belong to tenant {tenant_id}"

    payment_service = PaymentService(async_session_maker, bus, get_integration_connection_service())
    try:
        payment, _deduped = await payment_service.record_payment(
            tenant_id,
            customer_id=customer_id,
            amount=amount,
            provider="stripe",
            external_id=payment_intent_id,
            payment_method="card",
            allocations=[],
            quote_id=quote_id,
        )
    except (InvoiceNotFoundError, OverpaymentError) as exc:
        # Permanent, not transient — see the identical comment on the
        # other record_payment call above. Deliberately not the "failed:"
        # prefix _RETRYABLE_ERROR_PREFIXES matches on.
        return tenant_id, f"record_payment rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — reported to the caller as processing failure, not re-raised past the webhook boundary
        return tenant_id, f"record_payment failed: {exc}"

    quote_service = QuoteService(async_session_maker, bus)
    try:
        await quote_service.mark_deposit_paid(tenant_id, quote_id, payment_id=payment.id)
    except (QuoteNotFoundError, InvalidQuoteTransitionError) as exc:
        # The payment IS recorded (money genuinely moved) — a state
        # mismatch here (e.g. quote was somehow already CONVERTED) is
        # reported as a processing failure for investigation, never
        # silently swallowed, but it must never be re-attempted as if the
        # payment itself failed — this is a permanent state mismatch, not
        # the transient kind _RETRYABLE_ERROR_PREFIXES' "mark_deposit_paid
        # failed:" prefix is meant to catch, so this uses "rejected:"
        # instead and is deliberately excluded from Stripe's retry.
        return tenant_id, f"mark_deposit_paid rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — Phase 23 fix: mirrors the
        # record_payment try/except directly above. Previously any OTHER
        # failure here (e.g. a transient error inside JobService.create_job,
        # called from mark_deposit_paid -> _convert_to_job) propagated
        # uncaught past this function, past the webhook endpoint's own
        # generic handler, meaning `tenant_id` was never actually returned
        # to the caller — leaving the WebhookEvent audit row's own
        # `tenant_id` column stuck NULL, impossible to find/filter for
        # operator investigation. Catching it here — reported as a
        # processing failure, never re-raised past the webhook boundary —
        # keeps the audit trail correct and matches the redelivery fix
        # above it in this same file: once this event is genuinely
        # reprocessed, `mark_deposit_paid`'s own idempotent DEPOSIT_PAID/
        # CONVERTED check takes over correctly.
        return tenant_id, f"mark_deposit_paid failed: {exc}"

    return tenant_id, None


async def _handle_payment_intent_failed(
    envelope: StripeWebhookEnvelope, bus: EventBus
) -> tuple[uuid.UUID | None, str | None]:
    """A failed PaymentIntent never becomes a `Payment` row (nothing
    succeeded — there is no money movement to represent as one); this
    only publishes the existing `PAYMENT_FAILED` event so anything already
    subscribed to it (notifications, exception handling) reacts, and
    records the attempt in the WebhookEvent audit trail either way."""
    try:
        obj = StripePaymentIntentPayload.model_validate(envelope.data.object)
    except ValidationError as exc:
        return None, f"malformed PaymentIntent payload: {exc}"

    tenant_id_raw = obj.metadata.tenant_id
    invoice_id_raw = obj.metadata.invoice_id

    if not tenant_id_raw:
        return None, "missing tenant_id metadata on failed PaymentIntent"
    try:
        tenant_id = uuid.UUID(tenant_id_raw)
    except ValueError as exc:
        return None, f"malformed tenant_id in PaymentIntent metadata: {exc}"

    last_error = obj.last_payment_error.message if obj.last_payment_error else "unknown reason"

    await bus.publish(
        tenant_id=tenant_id,
        event_type=EventType.PAYMENT_FAILED,
        source="stripe_webhook",
        entity_type="invoice",
        entity_id=uuid.UUID(invoice_id_raw) if invoice_id_raw else None,
        payload={"provider": "stripe", "payment_intent_id": obj.id, "reason": last_error or "unknown reason"},
        idempotency_key=f"stripe-payment-failed-{obj.id}",
    )
    return tenant_id, None


async def _handle_charge_refunded(
    envelope: StripeWebhookEnvelope, bus: EventBus
) -> tuple[uuid.UUID | None, str | None]:
    """Reconciles a refund issued OUTSIDE Klaros (Stripe Dashboard, a
    dispute, etc.) against the matching Payment — see
    PaymentService.reconcile_external_refund for the idempotent,
    cumulative-total-based reconciliation logic. Never a Klaros-initiated
    refund path (that's finance.create_refund_request -> approval ->
    PaymentService.decide_refund, unchanged)."""
    try:
        obj = StripeChargePayload.model_validate(envelope.data.object)
    except ValidationError as exc:
        return None, f"malformed Charge payload: {exc}"

    tenant_id_raw = obj.metadata.tenant_id
    payment_intent_id = obj.payment_intent
    amount_refunded_cents = obj.amount_refunded

    if not (tenant_id_raw and payment_intent_id and amount_refunded_cents is not None):
        return None, "missing tenant_id metadata, payment_intent, or amount_refunded on Charge"
    try:
        tenant_id = uuid.UUID(tenant_id_raw)
    except ValueError as exc:
        return None, f"malformed tenant_id in Charge metadata: {exc}"

    total_refunded = Decimal(amount_refunded_cents) / Decimal(100)

    payment_service = PaymentService(async_session_maker, bus, get_integration_connection_service())
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


# --- Inbound Twilio lead capture ---------------------------------------
#
# The existing Twilio integration is platform-level (one shared
# TWILIO_ACCOUNT_SID/AUTH_TOKEN across every tenant, per
# IntegrationConnection's own docstring) and was previously outbound-SMS
# only. There is no per-tenant Twilio phone number or sub-account in this
# codebase — building that (who provisions a number per tenant? is it a
# paid feature? self-service via Twilio's Buy-a-Number API?) is a real
# product/pricing decision this repository does not establish, so it is
# NOT invented here.
#
# What IS safely buildable without that decision: the tenant embeds their
# own tenant_id in the webhook URL they paste into THEIR Twilio number's
# console configuration (a one-time, deliberate admin action — not
# attacker-controlled input; the same trust model as a per-tenant OAuth
# redirect URI elsewhere in this codebase). Twilio's HMAC signature is
# computed over that exact URL, so a request with a tampered tenant_id in
# the path fails signature verification. The raw caller's phone number
# (genuinely untrusted, anonymous input) is NEVER used to derive tenant
# identity — only to identify/match the Lead within the already-resolved
# tenant, via the same normalize_phone/find_matching_customer path every
# other lead-creation flow already uses.
#
# Canonical rule (unknown or known caller): always go through
# LeadService.create_lead — the one write path every other source already
# uses — idempotent on the real Twilio SID, source=PHONE/TEXT, no
# fabricated name/urgency/service. An unknown caller gets a plain,
# self-describing placeholder name built from their own number, never an
# invented identity. qualification_status is left at its normal default
# (PENDING) — the existing lead.created subscriber runs qualification
# exactly as it would for any other source.


def _twilio_service(bus: EventBus) -> LeadService:
    return LeadService(async_session_maker, bus)


_twilio_inbound_rate_limit = Depends(
    rate_limit(
        "twilio_inbound",
        limit_setting="RATE_LIMIT_WEBHOOK_PER_MINUTE",
        window_seconds=60,
        key_func=tenant_and_ip_key,
    )
)


@router.post("/twilio/inbound-sms/{tenant_id}", status_code=status.HTTP_200_OK, dependencies=[_twilio_inbound_rate_limit])
async def twilio_inbound_sms_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
) -> Response:
    settings = get_settings()
    if not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Twilio webhooks not configured")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    full_url = str(request.url)

    try:
        verify_twilio_signature(full_url, params, twilio_signature or "", settings.TWILIO_AUTH_TOKEN)
    except TwilioWebhookSignatureError as exc:
        logger.warning("twilio_inbound_sms_signature_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature") from exc

    message_sid = params.get("MessageSid", "")
    from_number = params.get("From", "")
    body = params.get("Body", "")

    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "twilio", WebhookEvent.external_event_id == f"inbound-sms:{message_sid}"
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return Response(content="<Response></Response>", media_type="application/xml")

        webhook_row = WebhookEvent(
            tenant_id=tenant_id,
            provider="twilio",
            external_event_id=f"inbound-sms:{message_sid}",
            event_type="inbound_sms",
            raw_payload=params,
            status=WebhookProcessingStatus.RECEIVED,
        )
        session.add(webhook_row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return Response(content="<Response></Response>", media_type="application/xml")

        webhook_row.status = WebhookProcessingStatus.PROCESSED
        await session.commit()

    normalized = normalize_phone(from_number) or from_number
    lead_service = _twilio_service(bus)
    await lead_service.create_lead(
        tenant_id,
        CreateLeadInput(
            name=f"Inbound SMS from {normalized}" if normalized else "Inbound SMS (unknown number)",
            source="TEXT",
            phone=from_number or None,
            description=body or None,
            idempotency_key=f"twilio-inbound-sms-{message_sid}",
        ),
    )

    return Response(content="<Response></Response>", media_type="application/xml")


@router.post(
    "/twilio/inbound-voice/{tenant_id}", status_code=status.HTTP_200_OK, dependencies=[_twilio_inbound_rate_limit]
)
async def twilio_inbound_voice_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    bus: EventBus = Depends(get_wired_event_bus),
) -> Response:
    """Twilio's inbound-call webhook. Never builds an IVR/menu system or a
    second voice architecture — the only real behaviors are: verify the
    call is genuine, capture the caller's number as a real Lead via the
    one canonical LeadService path, and answer with a short, honest
    acknowledgement (never a fabricated promise beyond what this business
    can actually do)."""
    settings = get_settings()
    if not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Twilio webhooks not configured")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    full_url = str(request.url)

    try:
        verify_twilio_signature(full_url, params, twilio_signature or "", settings.TWILIO_AUTH_TOKEN)
    except TwilioWebhookSignatureError as exc:
        logger.warning("twilio_inbound_voice_signature_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid signature") from exc

    call_sid = params.get("CallSid", "")
    from_number = params.get("From", "")
    ack = "<Response><Say>Thanks for calling. We have your number and will call you back shortly.</Say></Response>"

    # Phase 4: if this tenant has opted into the AI Voice Receptionist,
    # start a real CallSession and hand the call off to the Media Streams
    # WebSocket (app/api/v1/voice_stream.py) instead of the plain
    # capture-and-acknowledge behavior above — never both, and the legacy
    # behavior is completely unchanged for every tenant that hasn't
    # enabled it.
    voice_service = VoiceCallService(async_session_maker)
    voice_settings = await voice_service.get_settings(tenant_id)
    if voice_settings.enabled:
        call, _created = await voice_service.get_or_create_call(
            tenant_id, provider="twilio", external_call_id=call_sid, caller_number=from_number or None,
        )
        stream_url = str(request.url).replace("https://", "wss://").replace("http://", "ws://")
        stream_url = stream_url.split("/api/v1/webhooks/")[0] + "/api/v1/voice-stream"
        stream_twiml = (
            "<Response><Connect><Stream url=\"" + stream_url + "\">"
            f'<Parameter name="tenant_id" value="{tenant_id}"/>'
            f'<Parameter name="call_session_id" value="{call.id}"/>'
            "</Stream></Connect></Response>"
        )
        return Response(content=stream_twiml, media_type="application/xml")

    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "twilio", WebhookEvent.external_event_id == f"inbound-voice:{call_sid}"
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return Response(content=ack, media_type="application/xml")

        webhook_row = WebhookEvent(
            tenant_id=tenant_id,
            provider="twilio",
            external_event_id=f"inbound-voice:{call_sid}",
            event_type="inbound_voice",
            raw_payload=params,
            status=WebhookProcessingStatus.RECEIVED,
        )
        session.add(webhook_row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return Response(content=ack, media_type="application/xml")

        webhook_row.status = WebhookProcessingStatus.PROCESSED
        await session.commit()

    normalized = normalize_phone(from_number) or from_number
    lead_service = _twilio_service(bus)
    await lead_service.create_lead(
        tenant_id,
        CreateLeadInput(
            name=f"Inbound call from {normalized}" if normalized else "Inbound call (unknown number)",
            source="PHONE",
            phone=from_number or None,
            description=f"Inbound call, CallSid={call_sid}" if call_sid else None,
            idempotency_key=f"twilio-inbound-voice-{call_sid}",
        ),
    )

    return Response(content=ack, media_type="application/xml")
