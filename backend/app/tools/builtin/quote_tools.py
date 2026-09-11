"""Phase 14: quote lifecycle tools — the internal-staff half of the quote
domain (create/edit/send). The customer's own accept/decline happens
through the public view (`app/api/v1/public_quotes.py`), NOT through the
ToolRegistry — there is no authenticated `ExecutionContext` for an
unauthenticated customer, the same reasoning `app/api/v1/webhooks.py`
already established for Stripe/Twilio.
"""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import create_quote_view_token
from app.invoice_delivery.factory import get_invoice_delivery_provider
from app.models.quote import Quote
from app.models.rbac import Permission
from app.services.invoice_service import LineItemInput
from app.services.quote_service import (
    CustomerNotFoundError,
    InvalidDepositError,
    InvalidQuoteTransitionError,
    QuoteNotFoundError,
    QuoteService,
)
from app.tools.base import ExecutionContext, Tool


def _quote_to_dict(q: Quote) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "quote_number": q.quote_number,
        "customer_id": str(q.customer_id),
        "lead_id": str(q.lead_id) if q.lead_id else None,
        "job_id": str(q.job_id) if q.job_id else None,
        "status": q.status,
        "currency": q.currency,
        "subtotal": str(q.subtotal),
        "tax": str(q.tax),
        "discount": str(q.discount),
        "total": str(q.total),
        "notes": q.notes,
        "terms": q.terms,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "sent_at": q.sent_at.isoformat() if q.sent_at else None,
        "viewed_at": q.viewed_at.isoformat() if q.viewed_at else None,
        "decided_at": q.decided_at.isoformat() if q.decided_at else None,
        "decline_reason": q.decline_reason,
        "deposit_type": q.deposit_type,
        "deposit_value": str(q.deposit_value) if q.deposit_value is not None else None,
        "deposit_amount": str(q.deposit_amount) if q.deposit_amount is not None else None,
    }


class LineItemModel(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")


def _to_line_items(items: list[LineItemModel]) -> list[LineItemInput]:
    return [
        LineItemInput(
            description=i.description, quantity=i.quantity, unit_price=i.unit_price,
            discount=i.discount, tax_rate=i.tax_rate,
        )
        for i in items
    ]


class QuoteOutput(BaseModel):
    quote: dict[str, Any]
    deduplicated: bool = False


class CreateQuoteDraftInput(BaseModel):
    customer_id: uuid.UUID
    lead_id: uuid.UUID | None = None
    line_items: list[LineItemModel]
    notes: str | None = None
    terms: str | None = None
    idempotency_key: str | None = None
    # Phase 15: optional deposit requirement. deposit_type=None (the
    # default) means no deposit — accepting the quote behaves exactly as
    # before Phase 15.
    deposit_type: str | None = None
    deposit_value: Decimal | None = None


class CreateQuoteDraft(Tool):
    name = "quotes.create_quote_draft"
    description = "Create a DRAFT quote/estimate for a customer, priced from line items."
    input_schema = CreateQuoteDraftInput
    output_schema = QuoteOutput
    required_permission = Permission.CREATE_QUOTE

    def __init__(self, quote_service: QuoteService) -> None:
        self._quote_service = quote_service

    async def execute(self, input: CreateQuoteDraftInput, context: ExecutionContext) -> QuoteOutput:
        try:
            quote, deduplicated = await self._quote_service.create_draft(
                context.tenant_id,
                customer_id=input.customer_id,
                lead_id=input.lead_id,
                items=_to_line_items(input.line_items),
                notes=input.notes,
                terms=input.terms,
                idempotency_key=input.idempotency_key,
                deposit_type=input.deposit_type,
                deposit_value=input.deposit_value,
            )
        except (CustomerNotFoundError, InvalidDepositError) as e:
            raise ValueError(str(e)) from e
        return QuoteOutput(quote=_quote_to_dict(quote), deduplicated=deduplicated)


class UpdateQuoteDraftInput(BaseModel):
    quote_id: uuid.UUID
    line_items: list[LineItemModel]
    notes: str | None = None
    terms: str | None = None
    deposit_type: str | None = None
    deposit_value: Decimal | None = None
    clear_deposit: bool = False


class UpdateQuoteDraft(Tool):
    name = "quotes.update_quote_draft"
    description = "Replace a DRAFT quote's line items; totals are always recalculated server-side."
    input_schema = UpdateQuoteDraftInput
    output_schema = QuoteOutput
    required_permission = Permission.CREATE_QUOTE

    def __init__(self, quote_service: QuoteService) -> None:
        self._quote_service = quote_service

    async def execute(self, input: UpdateQuoteDraftInput, context: ExecutionContext) -> QuoteOutput:
        try:
            quote = await self._quote_service.update_draft(
                context.tenant_id, input.quote_id, items=_to_line_items(input.line_items),
                notes=input.notes, terms=input.terms,
                deposit_type=input.deposit_type, deposit_value=input.deposit_value,
                clear_deposit=input.clear_deposit,
            )
        except (QuoteNotFoundError, InvalidQuoteTransitionError, InvalidDepositError) as e:
            raise ValueError(str(e)) from e
        return QuoteOutput(quote=_quote_to_dict(quote))


class SendQuoteInput(BaseModel):
    quote_id: uuid.UUID


class SendQuoteOutput(BaseModel):
    quote: dict[str, Any]
    view_url_path: str


class SendQuote(Tool):
    """Marks the quote SENT, generates its real signed public view link,
    and delivers it via the existing `InvoiceDeliveryProvider` (extended
    in Phase 14 with `send_quote` — same provider/adapter, no parallel
    delivery path). `AUTO` policy — see app/tools/policy.py."""

    name = "quotes.send_quote"
    description = "Mark a DRAFT quote SENT and deliver its signed public view link to the customer."
    input_schema = SendQuoteInput
    output_schema = SendQuoteOutput
    required_permission = Permission.SEND_QUOTE

    def __init__(self, quote_service: QuoteService, session_factory) -> None:
        self._quote_service = quote_service
        self._delivery = get_invoice_delivery_provider(session_factory)

    async def execute(self, input: SendQuoteInput, context: ExecutionContext) -> SendQuoteOutput:
        settings = get_settings()
        token = create_quote_view_token(input.quote_id, context.tenant_id)
        view_url_path = f"/quotes/view/{input.quote_id}?token={token}"
        view_url = f"{settings.FRONTEND_BASE_URL}{view_url_path}"
        try:
            quote = await self._quote_service.send(context.tenant_id, input.quote_id, self._delivery, view_url)
        except (QuoteNotFoundError, InvalidQuoteTransitionError) as e:
            raise ValueError(str(e)) from e
        return SendQuoteOutput(quote=_quote_to_dict(quote), view_url_path=view_url_path)


class GetQuoteInput(BaseModel):
    quote_id: uuid.UUID


class GetQuote(Tool):
    name = "quotes.get_quote"
    description = "Fetch a quote by id."
    input_schema = GetQuoteInput
    output_schema = QuoteOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetQuoteInput, context: ExecutionContext) -> QuoteOutput:
        async with self._session_factory() as session:
            quote = await session.get(Quote, input.quote_id)
        if quote is None or quote.tenant_id != context.tenant_id:
            raise ValueError("Quote not found")
        return QuoteOutput(quote=_quote_to_dict(quote))


class DetectExpiredQuotesInput(BaseModel):
    pass


class DetectExpiredQuotesOutput(BaseModel):
    expired_quote_ids: list[str]


class DetectExpiredQuotes(Tool):
    name = "quotes.detect_expired_quotes"
    description = "Sweep SENT/VIEWED quotes past their valid_until date and mark them EXPIRED."
    input_schema = DetectExpiredQuotesInput
    output_schema = DetectExpiredQuotesOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, quote_service: QuoteService) -> None:
        self._quote_service = quote_service

    async def execute(self, input: DetectExpiredQuotesInput, context: ExecutionContext) -> DetectExpiredQuotesOutput:
        ids = await self._quote_service.detect_expired(context.tenant_id)
        return DetectExpiredQuotesOutput(expired_quote_ids=[str(i) for i in ids])
