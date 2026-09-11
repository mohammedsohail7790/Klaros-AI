"""Phase 15: staff-facing ToolRegistry tools for the quote-deposit
workflow. The customer's own deposit-checkout flow goes through the
public, token-secured router (app/api/v1/public_quotes.py) instead — no
authenticated ExecutionContext exists for an unauthenticated customer,
same reasoning already established for quote accept/decline in Phase 14.
These tools exist for a staff member to inspect deposit state or
generate a payment link manually (e.g. to read out over the phone).
"""

import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.quote_deposit_service import (
    DepositCheckoutError,
    DepositNotRequiredError,
    InvalidDepositStateError,
    QuoteDepositService,
    QuoteNotFoundError,
    StripeNotConfiguredError,
)
from app.tools.base import ExecutionContext, Tool


class GetQuoteDepositStatusInput(BaseModel):
    quote_id: uuid.UUID


class GetQuoteDepositStatusOutput(BaseModel):
    deposit_required: bool
    deposit_type: str | None
    deposit_value: str | None
    deposit_amount: str | None
    currency: str
    status: str


class GetQuoteDepositStatus(Tool):
    name = "finance.get_quote_deposit_status"
    description = "Read a quote's deposit configuration and current collection status."
    input_schema = GetQuoteDepositStatusInput
    output_schema = GetQuoteDepositStatusOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, deposit_service: QuoteDepositService) -> None:
        self._deposit_service = deposit_service

    async def execute(
        self, input: GetQuoteDepositStatusInput, context: ExecutionContext
    ) -> GetQuoteDepositStatusOutput:
        try:
            preview = await self._deposit_service.preview(context.tenant_id, input.quote_id)
        except QuoteNotFoundError as e:
            raise ValueError(str(e)) from e
        return GetQuoteDepositStatusOutput(
            deposit_required=preview.deposit_required,
            deposit_type=preview.deposit_type,
            deposit_value=str(preview.deposit_value) if preview.deposit_value is not None else None,
            deposit_amount=str(preview.deposit_amount) if preview.deposit_amount is not None else None,
            currency=preview.currency,
            status=preview.status,
        )


class CreateQuoteDepositCheckoutInput(BaseModel):
    quote_id: uuid.UUID
    success_url: str
    cancel_url: str


class CreateQuoteDepositCheckoutOutput(BaseModel):
    checkout_url: str
    checkout_session_id: str


class CreateQuoteDepositCheckoutSession(Tool):
    """Generates a real, hosted Stripe Checkout link for a quote's frozen
    deposit amount — the staff-initiated counterpart to the public
    customer-facing deposit-checkout endpoint; both call the same
    QuoteDepositService, no duplicated Stripe logic."""

    name = "finance.create_quote_deposit_checkout_session"
    description = "Generate a real, hosted Stripe Checkout payment link for a quote's deposit."
    input_schema = CreateQuoteDepositCheckoutInput
    output_schema = CreateQuoteDepositCheckoutOutput
    required_permission = Permission.COLLECT_PAYMENT

    def __init__(self, deposit_service: QuoteDepositService) -> None:
        self._deposit_service = deposit_service

    async def execute(
        self, input: CreateQuoteDepositCheckoutInput, context: ExecutionContext
    ) -> CreateQuoteDepositCheckoutOutput:
        try:
            result = await self._deposit_service.create_deposit_checkout_session(
                context.tenant_id, input.quote_id,
                success_url=input.success_url, cancel_url=input.cancel_url,
            )
        except (
            QuoteNotFoundError, DepositNotRequiredError, InvalidDepositStateError,
            StripeNotConfiguredError, DepositCheckoutError,
        ) as e:
            raise ValueError(str(e)) from e
        return CreateQuoteDepositCheckoutOutput(
            checkout_url=result.checkout_url, checkout_session_id=result.checkout_session_id
        )
