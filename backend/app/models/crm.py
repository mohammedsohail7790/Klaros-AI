import uuid
from enum import StrEnum

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class LeadSource(StrEnum):
    PHONE = "PHONE"
    VOICE = "VOICE"
    WEB = "WEB"
    CHAT = "CHAT"
    TEXT = "TEXT"
    DM = "DM"
    MARKETPLACE = "MARKETPLACE"
    REFERRAL = "REFERRAL"
    WALK_IN = "WALK_IN"
    # A lead brought in via crm.bulk_import_leads (an existing pipeline the
    # tenant is migrating into Klaros) — none of the above channels
    # honestly describe how it actually originated.
    OTHER = "OTHER"


class LeadStatus(StrEnum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    BOOKED = "BOOKED"
    LOST = "LOST"
    CONVERTED = "CONVERTED"


class QualificationStatus(StrEnum):
    PENDING = "PENDING"
    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


class Urgency(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EMERGENCY = "EMERGENCY"


class Lead(TenantScopedMixin, Base):
    __tablename__ = "leads"

    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_normalized: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    service_requested: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    urgency: Mapped[str] = mapped_column(String(20), nullable=False, default=Urgency.MEDIUM)
    estimated_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=LeadStatus.NEW, index=True)
    lead_score: Mapped[int | None] = mapped_column(nullable=True)
    score_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QualificationStatus.PENDING
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # The id of this lead in an external system that works on it (e.g. the AI workforce platform),
    # mirroring Customer/Appointment's external_provider/external_id. Klaros' own id stays the
    # lead's identity; this is only the link. NULL until a real sync returns one — never fabricated.
    external_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_leads_tenant_idempotency_key"),
        UniqueConstraint("tenant_id", "external_provider", "external_id", name="uq_leads_tenant_external_provider_id"),
    )


class CustomerStatus(StrEnum):
    PROSPECT = "PROSPECT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Customer(TenantScopedMixin, Base):
    __tablename__ = "customers"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_normalized: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CustomerStatus.PROSPECT)
    # Phase 13: mirrors Invoice.external_provider/external_id exactly — the
    # id of this customer's matching record in an external accounting
    # system (QuickBooks Online, ...), once synced. Nullable/unset until a
    # real sync actually creates one; never fabricated.
    external_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "external_provider", "external_id", name="uq_customers_tenant_external_provider_id"
        ),
    )


class CustomerNote(TenantScopedMixin, Base):
    """Freeform notes shown in the Customer 360 timeline (section 3/18)."""

    __tablename__ = "customer_notes"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    author_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ActorType
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class AppointmentStatus(StrEnum):
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


class Appointment(TenantScopedMixin, Base):
    __tablename__ = "appointments"

    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AppointmentStatus.TENTATIVE)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Phase 14: mirrors Invoice/Customer's external_provider/external_id
    # columns exactly — the id of this appointment's matching event in an
    # external calendar (Google Calendar, ...), once a real sync creates
    # one. Nullable/unset until then; never fabricated.
    external_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_appointments_tenant_idempotency_key"),
        UniqueConstraint(
            "tenant_id", "external_provider", "external_id", name="uq_appointments_tenant_external_provider_id"
        ),
    )
