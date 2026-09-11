"""Phase 12G: Pydantic schemas for the Stripe objects this application
actually reads or writes — deliberately NOT a reproduction of Stripe's
full API surface. Each model declares only the fields a real code path in
this codebase consumes and uses `extra="allow"` everywhere, so a field
Stripe adds later is preserved (reachable via `.model_extra`) rather than
rejected or silently dropped — parsing never breaks on an unknown field.

These schemas replace bare `dict`/`.get()` handling at the two Stripe
boundaries this app has: the inbound webhook envelope/event-object shape,
and outbound API response shapes. They do NOT replace the business-rule
validation that already exists one layer up (tenant ownership, UUID
well-formedness, legal state transitions) — a value can be a
schema-valid, well-typed string and still be the wrong tenant's id; that
check stays in `app/api/v1/webhooks.py`/`app/services/payment_service.py`
exactly as before.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StripeMetadata(BaseModel):
    """Only the metadata keys Klaros itself stamps onto Stripe objects
    (see `app/tools/builtin/stripe_tools.py::CreateStripeCheckoutSession`)
    are declared as typed fields; anything else present is preserved via
    `extra="allow"`, not dropped."""

    model_config = ConfigDict(extra="allow")

    tenant_id: str | None = None
    invoice_id: str | None = None
    customer_id: str | None = None
    # Phase 15: stamped only on a quote-deposit PaymentIntent/Checkout
    # Session (see app/services/quote_deposit_service.py). purpose
    # discriminates a deposit PaymentIntent from an invoice PaymentIntent
    # at the webhook boundary — invoice_id/quote_id are mutually exclusive
    # in practice, never both set on a real object this app created.
    purpose: str | None = None
    quote_id: str | None = None


class StripeLastPaymentError(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str | None = None
    code: str | None = None


class StripePaymentIntentPayload(BaseModel):
    """The subset of a Stripe `payment_intent` object this app reads, from
    the `payment_intent.succeeded`/`payment_intent.payment_failed` webhook
    events (`app/api/v1/webhooks.py`). `id` is the one field required to be
    present and a string — everything else this app treats as optional
    and validates for business-meaning (not just type) one layer up."""

    model_config = ConfigDict(extra="allow")

    id: str
    amount: int | None = None
    amount_received: int | None = None
    metadata: StripeMetadata = Field(default_factory=StripeMetadata)
    last_payment_error: StripeLastPaymentError | None = None


class StripeChargePayload(BaseModel):
    """The subset of a Stripe `charge` object this app reads, from the
    `charge.refunded` webhook event."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    payment_intent: str | None = None
    amount_refunded: int | None = None
    metadata: StripeMetadata = Field(default_factory=StripeMetadata)


class StripeWebhookEventData(BaseModel):
    """`data.object`'s concrete shape depends on the event's `type`
    (a PaymentIntent vs. a Charge vs. anything else) — kept as a raw
    `dict` here and validated into the correct specific payload schema by
    the handler for that event type, once `type` itself is known."""

    model_config = ConfigDict(extra="allow")

    object: dict[str, Any] = Field(default_factory=dict)


class StripeWebhookEnvelope(BaseModel):
    """The top-level Stripe Event envelope. `id`/`type` are the two fields
    every handler needs before it can even decide what to do with an
    event — a signed payload missing either is structurally malformed,
    which is a different failure mode than a well-formed, KNOWN event
    type whose business data happens to be incomplete (that case is
    handled per-event-type, recorded FAILED, still HTTP 200 — see
    `app/api/v1/webhooks.py`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    data: StripeWebhookEventData = Field(default_factory=StripeWebhookEventData)


class StripePaymentIntentResponse(BaseModel):
    """Response shape for `POST/GET .../payment_intents[/:id]` — replaces
    the previous hand-built dataclass with real validation of Stripe's
    response instead of unchecked dict indexing (`body["id"]` etc.)."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    amount: int
    currency: str
    client_secret: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class StripeCheckoutSessionResponse(BaseModel):
    """Response shape for `POST /checkout/sessions` — only the fields
    `finance.create_stripe_checkout_session` actually reads (`url`/`id`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    url: str
    payment_intent: str | None = None


class StripeRefundResponse(BaseModel):
    """Response shape for `POST /refunds`. Validated for schema-drift
    detection even though `PaymentService.decide_refund` does not
    currently persist Stripe's own refund id anywhere (the `Refund` model
    has no `external_id`/`provider` column, unlike `Payment` — a real,
    documented known limitation, not fixed in this pass since it would
    require a new migration; see PRODUCTION_AUDIT.md's Phase 12G-2
    section). If a future caller starts reading the response, this is the
    boundary it validates against."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    amount: int
    currency: str
    payment_intent: str | None = None
