import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    DISPATCHED = "DISPATCHED"
    EN_ROUTE = "EN_ROUTE"
    ON_SITE = "ON_SITE"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    QA_PENDING = "QA_PENDING"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class JobPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


class Job(TenantScopedMixin, Base):
    __tablename__ = "jobs"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    job_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.DRAFT, index=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default=JobPriority.NORMAL, index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    assigned_team_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_revenue: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_margin: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_revenue: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_margin: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    customer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_idempotency_key"),
    )


class WorkerStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"
    ON_LEAVE = "ON_LEAVE"
    INACTIVE = "INACTIVE"


class Worker(TenantScopedMixin, Base):
    __tablename__ = "workers"

    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WorkerStatus.AVAILABLE)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    service_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class JobTask(TenantScopedMixin, Base):
    __tablename__ = "job_tasks"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskStatus.PENDING)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class AttachmentKind(StrEnum):
    DOCUMENT = "DOCUMENT"
    PHOTO = "PHOTO"
    VOICE_NOTE = "VOICE_NOTE"


class TranscriptionStatus(StrEnum):
    PENDING = "PENDING"
    TRANSCRIBED = "TRANSCRIBED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class JobAttachment(TenantScopedMixin, Base):
    """Generalized attachment model covering documents, photos, and voice
    notes (section 16) — one table, discriminated by `kind`, rather than
    three near-identical ones."""

    __tablename__ = "job_attachments"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Voice-note-only fields; NULL for DOCUMENT/PHOTO kinds.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcription_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)


class MaterialStatus(StrEnum):
    REQUIRED = "REQUIRED"
    REQUESTED = "REQUESTED"
    ORDERED = "ORDERED"
    RECEIVED = "RECEIVED"
    USED = "USED"
    CANCELLED = "CANCELLED"


class JobMaterial(TenantScopedMixin, Base):
    __tablename__ = "job_materials"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimated_unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MaterialStatus.REQUIRED)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PurchaseOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    CANCELLED = "CANCELLED"


class PurchaseOrder(TenantScopedMixin, Base):
    __tablename__ = "purchase_orders"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    supplier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PurchaseOrderStatus.DRAFT)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="internal_draft")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PurchaseOrderItem(TenantScopedMixin, Base):
    __tablename__ = "purchase_order_items"

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_material_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimated_unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)


class ScopeChangeStatus(StrEnum):
    DETECTED = "DETECTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class ScopeChange(TenantScopedMixin, Base):
    __tablename__ = "scope_changes"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_revenue: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    margin_impact: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ScopeChangeStatus.DETECTED)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class ExceptionType(StrEnum):
    JOB_UNASSIGNED = "JOB_UNASSIGNED"
    JOB_DELAYED = "JOB_DELAYED"
    JOB_BLOCKED = "JOB_BLOCKED"
    JOB_OVERDUE = "JOB_OVERDUE"
    MISSING_REQUIRED_TASK = "MISSING_REQUIRED_TASK"
    MISSING_DOCUMENTATION = "MISSING_DOCUMENTATION"
    QA_FAILURE = "QA_FAILURE"
    SCOPE_CHANGE = "SCOPE_CHANGE"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    WORKER_CONFLICT = "WORKER_CONFLICT"
    CUSTOMER_WAITING = "CUSTOMER_WAITING"
    # Finance (Phase 5) — same exception engine, no second mechanism.
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    HIGH_AR = "HIGH_AR"
    MARGIN_LEAK = "MARGIN_LEAK"
    UNAPPROVED_REFUND = "UNAPPROVED_REFUND"
    UNUSUAL_DISCOUNT = "UNUSUAL_DISCOUNT"
    CASH_SHORTFALL = "CASH_SHORTFALL"
    VENDOR_BILL_OVERDUE = "VENDOR_BILL_OVERDUE"
    FORECAST_RISK = "FORECAST_RISK"
    # Marketing (Phase 6) — same exception engine, no second mechanism.
    CAMPAIGN_OVERSPEND = "CAMPAIGN_OVERSPEND"
    LOW_CONVERSION = "LOW_CONVERSION"
    HIGH_CAC = "HIGH_CAC"
    ATTRIBUTION_GAP = "ATTRIBUTION_GAP"
    CONTENT_APPROVAL_DELAY = "CONTENT_APPROVAL_DELAY"
    FAILED_PUBLICATION = "FAILED_PUBLICATION"
    REACTIVATION_FAILURE = "REACTIVATION_FAILURE"
    # Retention (Phase 7) — same exception engine, no second mechanism.
    CUSTOMER_AT_RISK = "CUSTOMER_AT_RISK"
    SERVICE_RECOVERY_REQUIRED = "SERVICE_RECOVERY_REQUIRED"
    NEGATIVE_FEEDBACK = "NEGATIVE_FEEDBACK"
    MISSED_FOLLOWUP = "MISSED_FOLLOWUP"
    REFERRAL_REWARD_REVIEW = "REFERRAL_REWARD_REVIEW"


class ExceptionSeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class OperationsException(TenantScopedMixin, Base):
    __tablename__ = "operations_exceptions"

    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ExceptionStatus.OPEN, index=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "type", "entity_id", "status",
            name="uq_exceptions_tenant_type_entity_open",
        ),
    )


class QAStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"


class JobQA(TenantScopedMixin, Base):
    __tablename__ = "job_qa"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=QAStatus.NOT_STARTED)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    performed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class PacketStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    SENT = "SENT"


class CompletionPacket(TenantScopedMixin, Base):
    __tablename__ = "completion_packets"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PacketStatus.DRAFT)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class CustomerSignoff(TenantScopedMixin, Base):
    """INTERNAL customer sign-off record (section 27) — not a legally binding
    e-signature; `signature_reference` is an internal token, not a claim of
    cryptographic/legal signing."""

    __tablename__ = "customer_signoffs"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    signature_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="internal_signoff")
