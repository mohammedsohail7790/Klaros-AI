"""Phase 15: creates the real, hosted Stripe Checkout Session a customer
uses to pay a quote's deposit. Deliberately reuses `create_checkout_session`
(the SAME hosted-Checkout pattern `finance.create_stripe_checkout_session`
already uses for invoices) rather than raw PaymentIntent + client_secret —
that would require Stripe.js/Elements on the frontend, which this
environment cannot build or verify. The customer is redirected to Stripe's
own page; confirmation always comes from the webhook
(`app/api/v1/webhooks.py`), never from the redirect itself.

No second Stripe client, no parallel credential-resolution logic — reuses
`StripeClient` and `resolve_stripe_secret_key` exactly as
`app/tools/builtin/stripe_tools.py` does.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.stripe_client import StripeAPIError, StripeClient
from app.models.quote import Quote, QuoteStatus
from app.services.integration_connection_service import IntegrationConnectionService
from app.tools.builtin.stripe_tools import resolve_stripe_secret_key


class QuoteNotFoundError(Exception):
    pass


class DepositNotRequiredError(Exception):
    pass


class InvalidDepositStateError(Exception):
    pass


class StripeNotConfiguredError(Exception):
    pass


class DepositCheckoutError(Exception):
    pass


@dataclass
class DepositPreview:
    deposit_required: bool
    deposit_type: str | None
    deposit_value: Decimal | None
    deposit_amount: Decimal | None
    currency: str
    status: str


@dataclass
class DepositCheckoutSession:
    checkout_url: str
    checkout_session_id: str


class QuoteDepositService:
    def __init__(
        self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService
    ) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def preview(self, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> DepositPreview:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            quote = await session.get(Quote, quote_id)
        if quote is None or quote.tenant_id != tenant_id:
            raise QuoteNotFoundError("Quote not found")
        return DepositPreview(
            deposit_required=quote.deposit_type is not None,
            deposit_type=quote.deposit_type,
            deposit_value=quote.deposit_value,
            deposit_amount=quote.deposit_amount,
            currency=quote.currency,
            status=quote.status,
        )

    async def create_deposit_checkout_session(
        self, tenant_id: uuid.UUID, quote_id: uuid.UUID, *, success_url: str, cancel_url: str,
    ) -> DepositCheckoutSession:
        secret_key = await resolve_stripe_secret_key(self._connection_service, tenant_id)
        if not secret_key:
            raise StripeNotConfiguredError(
                "Stripe is not connected (no tenant connection and STRIPE_SECRET_KEY not configured)"
            )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")
            if quote.deposit_type is None:
                raise DepositNotRequiredError("This quote has no deposit configured")
            if quote.status != QuoteStatus.DEPOSIT_PENDING:
                raise InvalidDepositStateError(
                    f"Cannot collect a deposit: quote is {quote.status}, expected DEPOSIT_PENDING"
                )
            if quote.deposit_amount is None or quote.deposit_amount <= 0:
                raise InvalidDepositStateError("Quote has no valid deposit amount to collect")
            deposit_amount = quote.deposit_amount
            currency = quote.currency
            quote_number = quote.quote_number
            customer_id = quote.customer_id

        client = StripeClient(secret_key)
        try:
            session_obj = await client.create_checkout_session(
                amount=deposit_amount,
                currency=currency,
                metadata={
                    "tenant_id": str(tenant_id),
                    "quote_id": str(quote_id),
                    "customer_id": str(customer_id),
                    "purpose": "quote_deposit",
                },
                success_url=success_url,
                cancel_url=cancel_url,
                description=f"Deposit for Quote {quote_number}",
                # Same quote + same frozen deposit amount -> same
                # idempotency key -> a duplicate/retried request (a
                # refreshed browser tab) gets Stripe's cached original
                # Checkout Session instead of a second one. deposit_amount
                # is immutable once DEPOSIT_PENDING (never recomputed), so
                # this key never legitimately changes for a given quote.
                idempotency_key=f"klaros-quote-deposit-{quote_id}-{deposit_amount}",
            )
        except StripeAPIError as exc:
            raise DepositCheckoutError(f"Stripe checkout session creation failed: {exc}") from exc

        return DepositCheckoutSession(checkout_url=session_obj.url, checkout_session_id=session_obj.id)
