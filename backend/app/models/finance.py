"""Phase 5: Finance & Back Office.

Money is `Decimal` everywhere (mapped from `Numeric`), never `float` — a
deliberate contrast with the pre-existing `Job.estimated_revenue` etc.
columns (Phase 4, `float`-mapped `Numeric`), which are left untouched per
"do not modify old migrations." New Finance code reads those Job columns
but never writes a float into a new Finance table.

`AccountReceivable` is *not* a persisted table here — section 14 explicitly
allows "a derived AR service if the architecture makes more sense," and AR
is fully derivable from `Invoice` rows (no sync-drift risk that way). See
`app/services/ar_service.py`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class InvoiceStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"
    CANCELLED = "CANCELLED"


class Invoice(TenantScopedMixin, Base):
    __tablename__ = "invoices"

    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=InvoiceStatus.DRAFT, index=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "invoice_number", name="uq_invoices_tenant_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_invoices_tenant_idempotency_key"),
    )


class InvoiceLineItem(TenantScopedMixin, Base):
    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    job_material_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    job_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class Payment(TenantScopedMixin, Base):
    __tablename__ = "payments"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PaymentStatus.PENDING, index=True)
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Phase 15: a deposit payment is tied directly to the Quote it's for —
    # unlike an invoice payment, there is no Invoice row yet to allocate
    # against via PaymentAllocation (that only happens once the deposit
    # converts the quote to a real Job/Invoice downstream). NULL for every
    # pre-Phase-15 (invoice) payment.
    quote_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    # Phase 17: the QuickBooks Payment this Klaros Payment was synced to,
    # once it has been. NOT the same slot as provider/external_id above —
    # those already identify this row's ORIGINATING provider (e.g.
    # "stripe" + a PaymentIntent id); this is a SEPARATE, secondary
    # accounting-sync target, the same relationship Invoice has with
    # QuickBooks via its own external_provider/external_id. NULL until a
    # real sync succeeds; its presence is this app's own idempotency
    # boundary against a duplicate sync (see QuickBooksPaymentSyncService).
    quickbooks_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "external_id", name="uq_payments_tenant_provider_external"),
    )


class PaymentAllocation(TenantScopedMixin, Base):
    __tablename__ = "payment_allocations"

    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class RefundStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


class Refund(TenantScopedMixin, Base):
    __tablename__ = "refunds"

    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RefundStatus.REQUESTED)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Phase 18: the QuickBooks RefundReceipt this Klaros Refund was synced
    # to, once it has been. Mirrors Payment.quickbooks_payment_id's role
    # exactly — its presence is this app's own idempotency boundary
    # against a duplicate sync. NULL until a real sync succeeds, and for
    # every pre-Phase-18 row.
    quickbooks_refund_receipt_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CreditNoteStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    VOID = "VOID"


class CreditNote(TenantScopedMixin, Base):
    __tablename__ = "credit_notes"

    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    credit_note_number: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CreditNoteStatus.DRAFT)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "credit_note_number", name="uq_credit_notes_tenant_number"),
    )


class CreditNoteLineItem(TenantScopedMixin, Base):
    __tablename__ = "credit_note_line_items"

    credit_note_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class WriteOffStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class WriteOffRequest(TenantScopedMixin, Base):
    __tablename__ = "writeoff_requests"

    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WriteOffStatus.REQUESTED)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class JobCostCategory(StrEnum):
    LABOR = "LABOR"
    MATERIAL = "MATERIAL"
    SUBCONTRACTOR = "SUBCONTRACTOR"
    TRAVEL = "TRAVEL"
    EQUIPMENT = "EQUIPMENT"
    OTHER = "OTHER"


class JobCost(TenantScopedMixin, Base):
    __tablename__ = "job_costs"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class VendorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Vendor(TenantScopedMixin, Base):
    __tablename__ = "vendors"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=VendorStatus.ACTIVE)


class VendorBillStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"


class VendorBill(TenantScopedMixin, Base):
    __tablename__ = "vendor_bills"

    vendor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=VendorBillStatus.DRAFT)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PayoutStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    PAID = "PAID"


class Payout(TenantScopedMixin, Base):
    __tablename__ = "payouts"

    vendor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PayoutStatus.REQUESTED)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="internal_test_payout")
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ForecastItemType(StrEnum):
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"


class ForecastConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CashForecast(TenantScopedMixin, Base):
    __tablename__ = "cash_forecasts"

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    starting_cash: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    starting_cash_source: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_CONNECTED")


class CashForecastItem(TenantScopedMixin, Base):
    __tablename__ = "cash_forecast_items"

    forecast_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PROJECTED")


class CollectionActionType(StrEnum):
    FRIENDLY_REMINDER = "FRIENDLY_REMINDER"
    SECOND_REMINDER = "SECOND_REMINDER"
    ESCALATION = "ESCALATION"
    OWNER_REVIEW = "OWNER_REVIEW"
    COLLECTION_REFERRAL = "COLLECTION_REFERRAL"


class CollectionActionStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    BLOCKED_CONSENT = "BLOCKED_CONSENT"  # consent guard refused the send; not retried, not delivered


class CollectionAction(TenantScopedMixin, Base):
    __tablename__ = "collection_actions"

    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CollectionActionStatus.PENDING)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "invoice_id", "action_type", name="uq_collection_actions_tenant_invoice_type"
        ),
    )
