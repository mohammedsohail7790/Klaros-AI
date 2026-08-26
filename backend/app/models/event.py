import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class EventStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class EventType(StrEnum):
    LEAD_CREATED = "lead.created"
    LEAD_UPDATED = "lead.updated"
    LEAD_ENRICHED = "lead.enriched"
    LEAD_QUALIFIED = "lead.qualified"
    LEAD_UNQUALIFIED = "lead.unqualified"
    LEAD_ASSIGNED = "lead.assigned"
    LEAD_BOOKED = "lead.booked"
    LEAD_LOST = "lead.lost"
    LEAD_CONVERTED = "lead.converted"

    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_MERGED = "customer.merged"

    APPOINTMENT_CREATED = "appointment.created"
    APPOINTMENT_UPDATED = "appointment.updated"
    APPOINTMENT_CANCELLED = "appointment.cancelled"
    APPOINTMENT_CONFIRMED = "appointment.confirmed"

    JOB_CREATED = "job.created"
    JOB_UPDATED = "job.updated"
    JOB_COMPLETED = "job.completed"

    INVOICE_CREATED = "invoice.created"
    INVOICE_OVERDUE = "invoice.overdue"
    PAYMENT_RECEIVED = "payment.received"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"

    INTEGRATION_FAILED = "integration.failed"


class Event(TenantScopedMixin, Base):
    """Durable event store (section 1). The Postgres row is the source of truth;
    the transport (Redis Streams today, Kafka/NATS later) only carries a pointer
    to it so business logic never depends on the transport's delivery guarantees.
    """

    __tablename__ = "events"

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=EventStatus.PUBLISHED)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_events_tenant_idempotency_key"),
    )


class ProcessingStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class EventProcessingRecord(TenantScopedMixin, Base):
    """One row per (event, handler). This is what actually prevents a duplicate
    delivery — from a retried webhook, a re-read stream message, or an explicit
    replay — from running the same business action twice: a handler only runs
    if no SUCCESS record already exists for this (event_id, handler_name) pair.
    """

    __tablename__ = "event_processing_records"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    handler_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", "handler_name", name="uq_event_processing_event_handler"),
    )


class DeadLetterEvent(TenantScopedMixin, Base):
    """Terminal record for an event/handler pair that exhausted its retry budget."""

    __tablename__ = "dead_letter_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    handler_name: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
