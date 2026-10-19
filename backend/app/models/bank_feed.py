"""Bank feed: accounts and transactions read from a business's bank (Plaid), plus the sync cursor.

Money convention (same as the rest of Klaros: `Decimal`, `Numeric(14, 2)`, never a float, amounts are positive magnitudes): `BankTransaction.amount` is ALWAYS a positive amount and
`direction` says which way it moved, `IN` (a deposit, money received) or `OUT` (money spent). Plaid reports the opposite sign (positive = money out, negative = money in); that flip happens
once, in `bank_feed_service`, and is the only place that knows about it.

A match is only a record linking a deposit to an invoice (`matched_entity_*`). The bank feed never creates, edits or settles a payment or invoice.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Date, DateTime, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class MatchStatus(StrEnum):
    UNMATCHED = "UNMATCHED"
    SUGGESTED = "SUGGESTED"
    MATCHED = "MATCHED"
    IGNORED = "IGNORED"


class BankAccount(TenantScopedMixin, Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "external_account_id", name="uq_bank_account_external"),)

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="plaid")
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mask: Mapped[str | None] = mapped_column(String(10), nullable=True)  # last digits only, as Plaid sends them
    type: Mapped[str | None] = mapped_column(String(40), nullable=True)  # depository | credit | loan | investment ...
    subtype: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BankTransaction(TenantScopedMixin, Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("tenant_id", "external_transaction_id", name="uq_bank_transaction_external"),)

    bank_account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    external_transaction_id: Mapped[str] = mapped_column(String(255), nullable=False)
    posted_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # positive magnitude, see the module docstring
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    merchant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # IN | OUT
    match_status: Mapped[str] = mapped_column(String(12), nullable=False, default=MatchStatus.UNMATCHED, index=True)
    matched_entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "invoice"
    matched_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    match_method: Mapped[str | None] = mapped_column(String(10), nullable=True)  # auto | manual
    match_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"invoice_id", "invoice_number", "score", "reason"}] while SUGGESTED


class BankFeedState(TenantScopedMixin, Base):
    """One row per business and provider: the /transactions/sync cursor and the honest outcome of the last sync."""

    __tablename__ = "bank_feed_state"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", name="uq_bank_feed_state_provider"),)

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="plaid")
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
