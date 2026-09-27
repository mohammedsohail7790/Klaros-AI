"""Phase 12C: real Stripe payment-link generation, exposed through the same
ToolRegistry -> permission -> tenant -> policy -> execution -> audit
pipeline as every other tool (no provider-specific shortcut). Actual
payment CONFIRMATION never happens here — only Stripe's signed webhook
(app/api/v1/webhooks.py) is trusted to record money as received, since a
Checkout redirect is not itself authenticated. This tool only ever
generates a request-for-payment; it moves no money and needs no approval,
same reasoning as finance.send_invoice.

Phase 12F: credential resolution now checks the tenant's own
IntegrationConnection("stripe") first (Phase 12D model) — a tenant with
their own connected Stripe account uses THEIR key, never another
tenant's — falling back to the platform-level Settings.STRIPE_SECRET_KEY
only when no tenant connection exists, preserving the pre-12F behavior
for tenants who haven't connected their own account. Also adds a real
Stripe idempotency key so a duplicate/retried request for the same
invoice never creates two separate Checkout Sessions.
"""

import uuid

from pydantic import BaseModel

from app.core.config import get_settings
from app.integrations.credential_store import decrypt_credential
from app.integrations.stripe_client import StripeAPIError, StripeClient
from app.models.finance import Invoice
from app.models.integration import ConnectionStatus
from app.models.rbac import Permission
from app.services.integration_connection_service import IntegrationConnectionService
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import ToolError


class CreateStripeCheckoutInput(BaseModel):
    invoice_id: uuid.UUID
    success_url: str
    cancel_url: str
    customer_email: str | None = None


class CreateStripeCheckoutOutput(BaseModel):
    checkout_url: str
    checkout_session_id: str


async def resolve_stripe_secret_key(
    connection_service: IntegrationConnectionService, tenant_id: uuid.UUID
) -> str | None:
    """Tenant's own connected Stripe account, if any; else the
    platform-level key. Never falls back to another tenant's credential —
    `connection_service.get_connection` is itself tenant-scoped (see
    app/services/integration_connection_service.py)."""
    connection = await connection_service.get_connection(tenant_id, "stripe")
    if connection is not None and connection.status == ConnectionStatus.CONNECTED and connection.encrypted_credential:
        credential = decrypt_credential(connection.encrypted_credential)
        tenant_key = credential.get("secret_key")
        if tenant_key:
            return tenant_key
    return get_settings().STRIPE_SECRET_KEY


class CreateStripeCheckoutSession(Tool):
    name = "finance.create_stripe_checkout_session"
    description = "Generate a real, hosted Stripe Checkout payment link for an invoice's amount due."
    input_schema = CreateStripeCheckoutInput
    output_schema = CreateStripeCheckoutOutput
    required_permission = Permission.RECORD_PAYMENT
    # Phase 7 (Agent Runtime Reliability II): verified true — Stripe's own
    # "Idempotency-Key" header (app/integrations/stripe_client.py) makes a
    # retried `create_checkout_session` call with the same key resolve to
    # Stripe's cached original response, never a second Checkout Session.
    # This tool already derives its own key from business identity
    # (invoice_id + amount_due, see below) rather than from
    # `context.idempotency_key` — a deliberately STRONGER guarantee than a
    # per-agent-step key, since it also dedupes a human's manual retry
    # through an entirely different AgentExecution for the same invoice,
    # not only a crash-recovery retry of the same step. Proven by
    # `tests/test_stripe_client.py` and this phase's own recovery tests.
    supports_idempotency = True

    def __init__(self, session_factory, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def execute(
        self, input: CreateStripeCheckoutInput, context: ExecutionContext
    ) -> CreateStripeCheckoutOutput:
        secret_key = await resolve_stripe_secret_key(self._connection_service, context.tenant_id)
        if not secret_key:
            raise ToolError("Stripe is not connected (no tenant connection and STRIPE_SECRET_KEY not configured)")

        async with self._session_factory() as session:
            invoice = await session.get(Invoice, input.invoice_id)
            if invoice is None or invoice.tenant_id != context.tenant_id:
                raise ToolError(f"Invoice {input.invoice_id} not found")
            if invoice.amount_due <= 0:
                raise ToolError(f"Invoice {invoice.invoice_number} has no amount due")
            amount_due = invoice.amount_due
            currency = invoice.currency
            invoice_number = invoice.invoice_number
            customer_id = invoice.customer_id

        client = StripeClient(secret_key)
        try:
            session_obj = await client.create_checkout_session(
                amount=amount_due,
                currency=currency,
                metadata={
                    "tenant_id": str(context.tenant_id),
                    "invoice_id": str(input.invoice_id),
                    "customer_id": str(customer_id),
                },
                success_url=input.success_url,
                cancel_url=input.cancel_url,
                description=f"Invoice {invoice_number}",
                customer_email=input.customer_email,
                # Same invoice + same amount due -> same idempotency key ->
                # a duplicate/retried request gets Stripe's cached original
                # response instead of a second, separate Checkout Session.
                # Amount is included so a legitimately new balance (e.g.
                # after a partial payment) is free to generate a new one.
                idempotency_key=f"klaros-checkout-{input.invoice_id}-{amount_due}",
            )
        except StripeAPIError as exc:
            raise ToolError(f"Stripe checkout session creation failed: {exc}") from exc

        return CreateStripeCheckoutOutput(
            checkout_url=session_obj.url, checkout_session_id=session_obj.id
        )
