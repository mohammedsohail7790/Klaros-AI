"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md, KLAROS_DOMAIN_
EXTENSIBILITY_SPEC.md §3, KLAROS_MEDICAL_TOURISM_VALIDATION.md): the Medical
Tourism vertical's minimum normalized domain model.

This is an ADDITIVE vertical extension, not a fork of core Klaros concepts.
Per the extension pattern (KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md §2 rule 2):
if a concept already exists in core (a lead, an appointment, a referral),
this module adds a one-to-one extension row referencing it by FK, never a
parallel entity; if a concept genuinely doesn't exist in core (a hospital,
a procedure catalog), it gets its own new, tenant-scoped table.

Seven new tables, matching KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md §3's
"minimum normalized model" exactly (no table added or dropped from that
list):

  - `Provider`            (new; hospital/clinic directory entity — no core
                            equivalent exists)
  - `ProviderCredential`  (new; licensing/accreditation records -> Provider)
  - `Procedure`           (new; treatment/procedure catalog — no core
                            equivalent exists)
  - `ProviderProcedure`   (new; join table — which providers offer which
                            procedures, at what estimated price)
  - `PatientLead`         (extends `Lead` 1:1 — medical-specific intake
                            fields only; qualification/scoring/conversion
                            reuse `LeadService` unchanged)
  - `Consultation`        (extends `Appointment` 1:1 — Google Calendar sync
                            reused unchanged)
  - `ReferralCommission`  (extends `Referral` 1:1 — cross-border commission
                            terms with a currency field, closing the gap
                            KLAROS_MEDICAL_TOURISM_VALIDATION.md's data-model
                            validation section identifies in the existing
                            `Referral`/`ReferralReward` retention models)

`Destination` is deliberately NOT its own table (KLAROS_DOMAIN_
EXTENSIBILITY_SPEC.md §3's explicit rejection) — folded into
`Provider.country`/`Provider.city`, which is sufficient for the validated
use case (two countries) and can be promoted to its own table later,
additively, without breaking anything. `Practitioner`/`TreatmentPackage`
are likewise rejected from the minimum set for the same documented
reasons (Provider.practitioner_name / Quote+QuoteLineItem reuse).

Every table here is `TenantScopedMixin` (standard app-layer tenant
isolation, RLS audit-mode instrumented in the same migration that creates
these tables — see alembic/versions/0049_medical_tourism_domain.py),
except none of these tables are global reference data: a Medical Tourism
provider directory is inherently tenant-owned business data (the
tenant's own curated list of partner hospitals/clinics), not a
platform-curated catalog like `VerticalExtension`/`IntegrationProviderCatalog`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class ProviderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Provider(TenantScopedMixin, Base):
    """A hospital/clinic directory entity. Standalone — no FK to any core
    entity required (KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md §3 table).
    `practitioner_name` folds the rejected standalone `Practitioner`
    concept in as an optional field for v1 (a hospital-level directory is
    sufficient for the validated use case)."""

    __tablename__ = "medical_tourism_providers"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    practitioner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)  # ISO 3166-1 alpha-2
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ProviderStatus.ACTIVE, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_medical_tourism_providers_tenant_idempotency_key"
        ),
    )


class CredentialStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class ProviderCredential(TenantScopedMixin, Base):
    """Licensing/accreditation records for a Provider. Supports the
    "compliance_verification" capability KLAROS_MEDICAL_TOURISM_
    VALIDATION.md's walkthrough identifies as a Recommendation surfaced
    for this vertical."""

    __tablename__ = "medical_tourism_provider_credentials"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True
    )
    credential_type: Mapped[str] = mapped_column(String(120), nullable=False)
    issuing_authority: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    issued_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CredentialStatus.PENDING, index=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcedureStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Procedure(TenantScopedMixin, Base):
    """Treatment/procedure catalog entry (e.g. "Hip Replacement",
    "Dental Implant"). Standalone — no core equivalent exists to extend."""

    __tablename__ = "medical_tourism_procedures"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    typical_destination_countries: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ProcedureStatus.ACTIVE, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_medical_tourism_procedures_tenant_idempotency_key"
        ),
    )


class ProviderProcedureStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ProviderProcedure(TenantScopedMixin, Base):
    """Join table: which providers offer which procedures, at what
    estimated price — the provider-inventory entity for this vertical
    (HARD SCOPE's "provider offerings"). A provider offering a given
    procedure more than once is nonsensical, so (tenant, provider,
    procedure) is unique -- this is also the row concurrent-creation
    tests (Phase 12) exercise against a real Postgres unique constraint."""

    __tablename__ = "medical_tourism_provider_procedures"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True
    )
    procedure_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_procedures.id"), nullable=False, index=True
    )
    estimated_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)  # ISO 4217
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProviderProcedureStatus.ACTIVE, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider_id", "procedure_id", name="uq_medical_tourism_provider_procedure_unique"
        ),
    )


class PatientLead(TenantScopedMixin, Base):
    """One-to-one extension of `Lead` (KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md
    §3: "`lead_id -> Lead.id` (one-to-one extension, not a new lead
    concept)"). Qualification, scoring, and conversion all reuse
    `LeadService` unchanged — this table only carries the medical-specific
    intake fields `Lead` itself has no business modeling."""

    __tablename__ = "medical_tourism_patient_leads"

    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    procedure_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_procedures.id"), nullable=True, index=True
    )
    preferred_destination_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    medical_history_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    travel_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    travel_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    has_insurance: Mapped[bool | None] = mapped_column(nullable=True)
    insurance_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "lead_id", name="uq_medical_tourism_patient_leads_tenant_lead"),
    )


class ConsultationStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class Consultation(TenantScopedMixin, Base):
    """One-to-one extension of `Appointment` (KLAROS_DOMAIN_EXTENSIBILITY_
    SPEC.md §3: "`appointment_id -> Appointment.id` (extension)"). Google
    Calendar sync (app/tools/builtin/google_calendar_tools.py) is reused
    unchanged via the underlying `Appointment` row -- this table only adds
    the provider link and a Medical-Tourism-specific consultation status."""

    __tablename__ = "medical_tourism_consultations"

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("appointments.id"), nullable=False, index=True
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True
    )
    procedure_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_procedures.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ConsultationStatus.SCHEDULED, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "appointment_id", name="uq_medical_tourism_consultations_tenant_appointment"
        ),
    )


class ReferralCommissionBasis(StrEnum):
    PERCENTAGE = "PERCENTAGE"
    FLAT = "FLAT"


class ReferralCommissionStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class ReferralCommission(TenantScopedMixin, Base):
    """One-to-one extension of the existing retention `Referral` model
    (KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md §3: "`referral_id -> Referral.id`
    (extends, not replaces, the existing retention Referral model)").
    Adds the currency field that KLAROS_MEDICAL_TOURISM_VALIDATION.md's
    data-model validation section verifies is genuinely missing from
    `Referral`/`ReferralReward` (`retention.py:273-325` has no currency
    column on either). A confirmed commission still becomes a normal
    `ReferralReward` row via the existing, unchanged
    PENDING->APPROVED->ISSUED lifecycle -- this table only supplies the
    computed amount/currency/basis; it never issues a payment itself."""

    __tablename__ = "medical_tourism_referral_commissions"

    referral_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("referrals.id"), nullable=False, index=True
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=True, index=True
    )
    basis: Mapped[str] = mapped_column(String(20), nullable=False, default=ReferralCommissionBasis.PERCENTAGE)
    commission_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    flat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    computed_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217, required — the exact gap closed
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReferralCommissionStatus.PENDING, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "referral_id", name="uq_medical_tourism_referral_commissions_tenant_referral"
        ),
    )
