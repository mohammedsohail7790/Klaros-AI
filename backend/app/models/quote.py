"""Phase 14: Quotes/Estimates — the pre-work stage the pipeline was
missing (Lead -> Job -> Invoice existed; nothing modeled "propose a price,
let the customer approve it before any work starts"). A `Quote` converts
into a real `Job` only once the CUSTOMER accepts it — the customer's own
accept/decline decision (via the public, token-secured view — see
`app/services/quote_service.py` and `app/core/security.py::
create_quote_view_token`) is the approval boundary here, distinct from
Finance's internal-staff `ApprovalRequest` pattern, since no Klaros staff
member's approval is what's being modeled.

Same `Decimal`-via-`Numeric` convention as `app/models/finance.py`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class QuoteStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    VIEWED = "VIEWED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    # Phase 15: only reached when the quote has a deposit configured — the
    # customer accepted, but the commitment isn't yet finalized until the
    # deposit is actually paid. A quote with no deposit configured skips
    # both of these and goes ACCEPTED -> CONVERTED exactly as before.
    DEPOSIT_PENDING = "DEPOSIT_PENDING"
    DEPOSIT_PAID = "DEPOSIT_PAID"
    CONVERTED = "CONVERTED"  # a real Job now exists from this quote


class DepositType(StrEnum):
    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"


class Quote(TenantScopedMixin, Base):
    __tablename__ = "quotes"

    quote_number: Mapped[str] = mapped_column(String(50), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=QuoteStatus.DRAFT, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Same "retried request never creates two rows" pattern as
    # Invoice/Appointment/Lead's own idempotency_key columns.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Phase 15: deposit configuration, set (optionally) while the quote is
    # still a DRAFT. NULL deposit_type means no deposit is required — the
    # accept flow is then byte-for-byte the pre-Phase-15 behavior.
    # deposit_value is the configured input (a currency amount for FIXED,
    # a 0-100 percentage for PERCENTAGE); deposit_amount is the computed,
    # frozen dollar amount actually due, set once at accept time and never
    # recomputed afterward (immutable once payment collection begins).
    deposit_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    deposit_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "quote_number", name="uq_quotes_tenant_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_quotes_tenant_idempotency_key"),
    )


class QuoteLineItem(TenantScopedMixin, Base):
    __tablename__ = "quote_line_items"

    quote_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
