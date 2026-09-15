"""Business compliance tracking — licenses, insurance policies, bonds,
and certifications with real expiry dates. Matches the "Licence &
liability" input the owner is expected to maintain themselves (an
external-authority document, never something the business can auto-renew
on its own) plus the exception/detection engine every other domain in
this codebase already uses for time-based risk (see
app/services/delay_detection_service.py, ContractService.detect_pending)."""

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import Date, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class LicenseType(StrEnum):
    BUSINESS_LICENSE = "BUSINESS_LICENSE"
    CONTRACTOR_LICENSE = "CONTRACTOR_LICENSE"
    LIABILITY_INSURANCE = "LIABILITY_INSURANCE"
    WORKERS_COMP_INSURANCE = "WORKERS_COMP_INSURANCE"
    BONDING = "BONDING"
    CERTIFICATION = "CERTIFICATION"
    PERMIT = "PERMIT"
    OTHER = "OTHER"


class LicenseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"


class License(TenantScopedMixin, Base):
    __tablename__ = "licenses"

    type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issuing_authority: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Who/what the license covers — a specific worker's name, a vehicle, or
    # left blank for a business-wide document (e.g. the liability policy).
    holder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    holder_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=LicenseStatus.ACTIVE)
    document_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
