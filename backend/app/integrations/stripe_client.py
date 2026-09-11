"""Real Stripe API client (Phase 12C, hardened Phase 12F).

Direct httpx calls against Stripe's REST API (Bearer auth with the secret
key, `application/x-www-form-urlencoded` bodies — Stripe's API does not
accept JSON), matching this project's established pattern for real
external HTTP integrations (see app/services/ai_provider.py) rather than
adding the `stripe` SDK as a dependency.

Webhook signature verification is implemented directly per Stripe's
documented scheme (HMAC-SHA256 over "{timestamp}.{raw_body}", using the
webhook signing secret) — see `verify_webhook_signature`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from app.core.config import get_settings
from app.integrations.stripe_schemas import (
    StripeCheckoutSessionResponse,
    StripePaymentIntentResponse,
    StripeRefundResponse,
)

logger = structlog.get_logger(__name__)

_API_BASE = "https://api.stripe.com/v1"


class StripeErrorType(StrEnum):
    """Phase 12F: real error classification, matching the pattern already
    established in app/services/ai_provider.py's AIErrorType — lets a
    caller (or the audit/observability layer) distinguish an invalid key
    from a transient rate limit from a genuinely malformed request."""

    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"


class StripeAPIError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, error_type: StripeErrorType = StripeErrorType.PROVIDER_ERROR
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


class StripeWebhookSignatureError(Exception):
    pass


class StripeWebhookPayloadError(Exception):
    """Raised when a webhook's signature verifies correctly but the body
    beneath it isn't valid JSON. Deliberately a different exception type
    than `StripeWebhookSignatureError` — the trust boundary (is this
    really from Stripe?) already passed; this is a distinct, later failure
    (is the body Stripe claims to have sent even well-formed?), and the
    two deserve different log messages/HTTP details even though both end
    up as a 400 at the webhook endpoint."""

    pass


# Retained as the public name `StripePaymentIntent` (Phase 12C-12F name)
# for import compatibility, now a real Pydantic model instead of a
# dataclass — Stripe's response is validated against it instead of
# trusted via unchecked dict indexing.
StripePaymentIntent = StripePaymentIntentResponse


def _validate_response(model: type[Any], body: dict[str, Any]) -> Any:
    """Validates a Stripe API response body against the schema this app
    expects for it. A `ValidationError` here means Stripe's response
    doesn't have the shape this client relies on (a genuine, if rare,
    provider-side surprise — e.g. a field this code depends on being
    absent) — surfaced as a `StripeAPIError` (provider_error) rather than
    an unhandled `pydantic.ValidationError`, consistent with every other
    Stripe failure this client raises."""
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise StripeAPIError(
            f"Stripe returned an unexpected response shape: {exc}", error_type=StripeErrorType.PROVIDER_ERROR
        ) from exc


class StripeClient:
    """One real client per call — no shared connection pool across
    requests, matching AnthropicAIProvider/OpenAIAIProvider's pattern.
    Never logs or raises the API key itself; only ever the last 4 chars
    for operator debugging (`sk_***...1234`)."""

    def __init__(self, secret_key: str) -> None:
        self._secret_key = secret_key
        settings = get_settings()
        self._timeout_seconds = settings.STRIPE_TIMEOUT_SECONDS
        self._max_retries = settings.STRIPE_MAX_RETRIES

    def __repr__(self) -> str:
        return "StripeClient(is_connected=True)"

    async def _request(
        self, method: str, path: str, *, data: dict[str, Any] | None = None, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        url = f"{_API_BASE}{path}"
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.request(
                        method, url, data=data, auth=(self._secret_key, ""), headers=headers
                    )
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("stripe_request_attempt_failed", attempt=attempt, error_type="timeout", retryable=True)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise StripeAPIError(
                    f"Stripe request timed out after {self._max_retries} attempts", error_type=StripeErrorType.TIMEOUT
                ) from exc
            except httpx.HTTPError as exc:
                # Connection-level failure (DNS, refused, etc.) — retryable,
                # same reasoning as a timeout: transient, not the caller's fault.
                last_exc = exc
                logger.warning("stripe_request_attempt_failed", attempt=attempt, error_type="network_error", retryable=True)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise StripeAPIError(
                    f"Stripe request failed: {exc}", error_type=StripeErrorType.NETWORK_ERROR
                ) from exc

            if response.status_code == 401 or response.status_code == 403:
                # Never retry an invalid/revoked key — retrying wastes time
                # and quota on something that cannot succeed.
                body = response.json() if response.content else {}
                message = body.get("error", {}).get("message", response.text)
                raise StripeAPIError(message, status_code=response.status_code, error_type=StripeErrorType.AUTHENTICATION)

            if response.status_code == 429 or response.status_code >= 500:
                error_type = StripeErrorType.RATE_LIMIT if response.status_code == 429 else StripeErrorType.PROVIDER_ERROR
                logger.warning(
                    "stripe_request_attempt_failed", attempt=attempt, error_type=error_type.value,
                    status_code=response.status_code, retryable=True,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                body = response.json() if response.content else {}
                message = body.get("error", {}).get("message", response.text)
                raise StripeAPIError(message, status_code=response.status_code, error_type=error_type)

            if response.status_code >= 400:
                # 400/402/404/422 etc. — a malformed or invalid request;
                # retrying an unchanged request would just fail identically.
                body = response.json() if response.content else {}
                message = body.get("error", {}).get("message", response.text)
                raise StripeAPIError(message, status_code=response.status_code, error_type=StripeErrorType.INVALID_REQUEST)

            return response.json()

        raise StripeAPIError(f"Stripe request failed after retries: {last_exc}", error_type=StripeErrorType.NETWORK_ERROR)

    async def verify_connection(self) -> bool:
        """A cheap, real read-only call to prove the key actually works —
        used by StripeAdapter.get_status() to distinguish 'key is set' from
        'key is set and Stripe accepts it'."""
        try:
            await self._request("GET", "/balance")
            return True
        except StripeAPIError:
            return False

    async def create_payment_intent(
        self,
        *,
        amount: Decimal,
        currency: str,
        metadata: dict[str, str],
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> StripePaymentIntent:
        """`amount` in the invoice's own currency's major unit (e.g. dollars);
        Stripe wants the minor unit (cents) as an integer."""
        cents = int((amount * 100).to_integral_value())
        data: dict[str, Any] = {
            "amount": cents,
            "currency": currency.lower(),
            "automatic_payment_methods[enabled]": "true",
        }
        if description:
            data["description"] = description
        for k, v in metadata.items():
            data[f"metadata[{k}]"] = v
        body = await self._request("POST", "/payment_intents", data=data, idempotency_key=idempotency_key)
        return _validate_response(StripePaymentIntentResponse, body)

    async def create_checkout_session(
        self,
        *,
        amount: Decimal,
        currency: str,
        metadata: dict[str, str],
        success_url: str,
        cancel_url: str,
        description: str | None = None,
        customer_email: str | None = None,
        idempotency_key: str | None = None,
    ) -> StripeCheckoutSessionResponse:
        """A hosted Stripe Checkout page — the practical, verifiable real
        payment flow for a product with no custom card-collection frontend
        yet: the customer is redirected to Stripe's own page, pays with a
        real (or, in test mode, a documented test) card, and Stripe
        redirects back. Confirmation always comes from the webhook, never
        from the redirect itself (the redirect is not authenticated).
        `idempotency_key` (Phase 12F): a retried checkout-session creation
        for the SAME invoice (e.g. a client double-click, or a retry after
        a network blip) must never create two separate Stripe Checkout
        Sessions/PaymentIntents for one invoice."""
        cents = int((amount * 100).to_integral_value())
        data: dict[str, Any] = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": cents,
            "line_items[0][price_data][product_data][name]": description or "Invoice payment",
            "line_items[0][quantity]": 1,
        }
        if customer_email:
            data["customer_email"] = customer_email
        for k, v in metadata.items():
            data[f"metadata[{k}]"] = v
            # Also stamp the PaymentIntent Checkout creates, since the
            # webhook we act on is payment_intent.succeeded, not the
            # checkout session event.
            data[f"payment_intent_data[metadata][{k}]"] = v
        body = await self._request("POST", "/checkout/sessions", data=data, idempotency_key=idempotency_key)
        return _validate_response(StripeCheckoutSessionResponse, body)

    async def retrieve_payment_intent(self, payment_intent_id: str) -> StripePaymentIntent:
        body = await self._request("GET", f"/payment_intents/{payment_intent_id}")
        return _validate_response(StripePaymentIntentResponse, body)

    async def create_refund(
        self,
        *,
        payment_intent_id: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> StripeRefundResponse:
        data: dict[str, Any] = {"payment_intent": payment_intent_id}
        if amount is not None:
            data["amount"] = int((amount * 100).to_integral_value())
        if reason:
            data["reason"] = reason if reason in ("duplicate", "fraudulent", "requested_by_customer") else None
            data["metadata[reason_detail]"] = reason
        # Idempotency-Key: a retried decide_refund() (concurrent call, or a
        # client retry) for the same refund_id must never issue two real
        # refunds at Stripe — Stripe itself dedupes any request carrying
        # the same key within a 24h window, regardless of this
        # application's own concurrency behavior.
        body = await self._request("POST", "/refunds", data=data, idempotency_key=idempotency_key)
        return _validate_response(StripeRefundResponse, body)


def verify_webhook_signature(payload: bytes, sig_header: str, webhook_secret: str, *, tolerance_seconds: int = 300) -> dict[str, Any]:
    """Stripe's documented scheme: the `Stripe-Signature` header is a
    comma-separated list of `t=<timestamp>,v1=<signature>[,v0=...]`. The
    expected signature is HMAC-SHA256("{timestamp}.{payload}", webhook_secret).
    Rejects (raises StripeWebhookSignatureError) on any mismatch OR a
    timestamp outside `tolerance_seconds` of now (replay-attack defense).
    Returns the parsed JSON body ONLY after verification succeeds — callers
    must never parse/act on the body before calling this."""
    if not sig_header:
        raise StripeWebhookSignatureError("missing Stripe-Signature header")

    parts: dict[str, list[str]] = {}
    for item in sig_header.split(","):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        parts.setdefault(key.strip(), []).append(value.strip())

    timestamps = parts.get("t")
    signatures = parts.get("v1")
    if not timestamps or not signatures:
        raise StripeWebhookSignatureError("malformed Stripe-Signature header")

    timestamp = timestamps[0]
    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise StripeWebhookSignatureError("non-numeric timestamp in Stripe-Signature") from exc

    if abs(time.time() - ts_int) > tolerance_seconds:
        raise StripeWebhookSignatureError("Stripe-Signature timestamp outside tolerance (possible replay)")

    signed_payload = f"{timestamp}.".encode() + payload
    expected_sig = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    if not any(hmac.compare_digest(expected_sig, sig) for sig in signatures):
        raise StripeWebhookSignatureError("signature mismatch")

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        # The signature is genuinely valid — this really is bytes Stripe
        # (or whoever holds the webhook secret) signed — but the bytes
        # underneath aren't valid JSON. A different failure mode than a
        # bad signature, so a distinct exception type; the webhook
        # endpoint maps both to a 400, with different log messages/detail.
        raise StripeWebhookPayloadError(f"malformed JSON in webhook payload: {exc}") from exc
