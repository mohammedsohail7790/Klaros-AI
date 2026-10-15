"""What each clinic has agreed to pay: the commercial terms the automation reads. Nothing here is assumed: a clinic with no terms gets no commission and no invoice, and says so."""

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class ProviderTerms(TenantScopedMixin, Base):
    __tablename__ = "medical_tourism_provider_terms"
    __table_args__ = (UniqueConstraint("tenant_id", "provider_id", name="uq_mt_provider_terms"),)

    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True)
    commission_basis: Mapped[str | None] = mapped_column(String(20), nullable=True)  # PERCENTAGE | FLAT
    commission_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    flat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    qualifying_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    listing_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    listing_fee_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    listing_fee_interval: Mapped[str | None] = mapped_column(String(10), nullable=True)  # MONTHLY | NONE
    agreement_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # where the signed agreement is kept (a reference, not its text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
