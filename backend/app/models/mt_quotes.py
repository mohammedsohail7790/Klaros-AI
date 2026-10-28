"""Clinic quotes: what a clinic has quoted, in writing, for one patient's enquiry. Only the figures and what they include are kept here; the written quote itself stays where the clinic sent it (`reference` says where)."""

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class QuoteStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"  # the same clinic quoted again for the same enquiry and procedure
    WITHDRAWN = "WITHDRAWN"


class ClinicQuote(TenantScopedMixin, Base):
    __tablename__ = "medical_tourism_clinic_quotes"

    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True)
    procedure_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_procedures.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    includes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    excludes: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # where the written quote is kept (a reference, not its text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=QuoteStatus.ACTIVE, index=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
