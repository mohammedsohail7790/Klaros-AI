"""Phase 13: Pydantic schemas for the QuickBooks Online objects this
application actually reads or writes — not a reproduction of Intuit's
full API surface. Every model uses `extra="allow"` so a field QuickBooks
adds later is preserved, never rejected, matching the pattern established
for Stripe in `app/integrations/stripe_schemas.py`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuickBooksTokenResponse(BaseModel):
    """Response shape from Intuit's OAuth2 token endpoint
    (`/oauth2/v1/tokens/bearer`), for both the initial code exchange and a
    refresh-token grant. `x_refresh_token_expires_in` is Intuit's own
    (non-standard) field name, kept verbatim rather than renamed."""

    model_config = ConfigDict(extra="allow")

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    x_refresh_token_expires_in: int | None = None


class QuickBooksCompanyInfo(BaseModel):
    """The subset of `CompanyInfo` this app reads — used only as a real,
    cheap, read-only verification call (mirrors `StripeClient.
    verify_connection`'s `GET /v1/balance`)."""

    model_config = ConfigDict(extra="allow")

    CompanyName: str | None = None
    Id: str | None = None


class QuickBooksCustomerInput(BaseModel):
    """Only the fields this app sends when creating a QuickBooks Customer
    from a Klaros `Customer` row."""

    model_config = ConfigDict(extra="allow")

    DisplayName: str
    PrimaryEmailAddr: dict[str, str] | None = None
    PrimaryPhone: dict[str, str] | None = None


class QuickBooksCustomerResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    Id: str
    DisplayName: str | None = None


class QuickBooksInvoiceLineInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    Amount: float
    Description: str | None = None
    DetailType: str = "SalesItemLineDetail"


class QuickBooksInvoiceInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    CustomerRef: dict[str, str]
    Line: list[dict[str, Any]]
    DocNumber: str | None = None


class QuickBooksInvoiceResponse(BaseModel):
    """The subset of a created QBO `Invoice` this app reads back —
    `Id` is what gets stored on `Invoice.external_id`."""

    model_config = ConfigDict(extra="allow")

    Id: str
    DocNumber: str | None = None
    TotalAmt: float | None = None


class QuickBooksPaymentInput(BaseModel):
    """Only the fields this app sends when recording a payment against a
    QBO Invoice (`POST /payment`) — `Line[].LinkedTxn` is what actually
    applies the payment to the invoice, mirroring how QuickBooks' own UI
    represents "receive payment against an invoice"."""

    model_config = ConfigDict(extra="allow")

    TotalAmt: float
    CustomerRef: dict[str, str]
    Line: list[dict[str, Any]]


class QuickBooksPaymentResponse(BaseModel):
    """The subset of a created QBO `Payment` this app reads back — `Id` is
    what gets stored on `Payment.quickbooks_payment_id`."""

    model_config = ConfigDict(extra="allow")

    Id: str
    TotalAmt: float | None = None


class QuickBooksRefundReceiptInput(BaseModel):
    """Only the fields this app sends when recording money refunded back
    to a customer (`POST /refundreceipt`) — QuickBooks' own documented
    object for "you already received payment and are now giving money
    back," distinct from a CreditMemo (an unapplied credit toward future
    purchases). `Line[].LinkedTxn` ties the refund back to the original
    QBO Payment it's reversing, mirroring how Payment.Line[].LinkedTxn
    ties a payment to the invoice it's applied against."""

    model_config = ConfigDict(extra="allow")

    TotalAmt: float
    CustomerRef: dict[str, str]
    Line: list[dict[str, Any]]


class QuickBooksRefundReceiptResponse(BaseModel):
    """The subset of a created QBO `RefundReceipt` this app reads back —
    `Id` is what gets stored on `Refund.quickbooks_refund_receipt_id`."""

    model_config = ConfigDict(extra="allow")

    Id: str
    TotalAmt: float | None = None


class QuickBooksErrorDetail(BaseModel):
    """QuickBooks wraps API errors as `{"Fault": {"Error": [{"Message":
    ..., "code": ...}]}}` — distinct shape from Stripe's `{"error": {...}}`,
    handled by its own schema rather than forcing a shared one."""

    model_config = ConfigDict(extra="allow")

    Message: str | None = None
    Detail: str | None = None
    code: str | None = None


class QuickBooksFault(BaseModel):
    model_config = ConfigDict(extra="allow")

    Error: list[QuickBooksErrorDetail] = Field(default_factory=list)


class QuickBooksErrorResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    Fault: QuickBooksFault | None = None
