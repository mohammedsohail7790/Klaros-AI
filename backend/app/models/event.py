import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class EventStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
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
    JOB_SCHEDULED = "job.scheduled"
    JOB_RESCHEDULED = "job.rescheduled"
    JOB_ASSIGNED = "job.assigned"
    JOB_UNASSIGNED = "job.unassigned"
    JOB_DISPATCHED = "job.dispatched"
    JOB_EN_ROUTE = "job.en_route"
    JOB_ON_SITE = "job.on_site"
    JOB_STARTED = "job.started"
    JOB_BLOCKED = "job.blocked"
    JOB_UNBLOCKED = "job.unblocked"
    JOB_DELAYED = "job.delayed"
    JOB_COMPLETED = "job.completed"
    JOB_QA_STARTED = "job.qa_started"
    JOB_QA_FAILED = "job.qa_failed"
    JOB_QA_PASSED = "job.qa_passed"
    JOB_CLOSED = "job.closed"
    JOB_CANCELLED = "job.cancelled"

    SCOPE_CHANGE_DETECTED = "scope_change.detected"
    SCOPE_CHANGE_APPROVED = "scope_change.approved"
    SCOPE_CHANGE_REJECTED = "scope_change.rejected"

    EXCEPTION_CREATED = "exception.created"
    EXCEPTION_RESOLVED = "exception.resolved"

    INVOICE_CREATED = "invoice.created"
    INVOICE_UPDATED = "invoice.updated"
    INVOICE_APPROVAL_REQUESTED = "invoice.approval_requested"
    INVOICE_APPROVED = "invoice.approved"
    INVOICE_REJECTED = "invoice.rejected"
    INVOICE_SENT = "invoice.sent"
    INVOICE_OVERDUE = "invoice.overdue"
    INVOICE_PAID = "invoice.paid"
    INVOICE_PARTIALLY_PAID = "invoice.partially_paid"
    INVOICE_VOIDED = "invoice.voided"
    INVOICE_TRIGGER_REQUESTED = "invoice.trigger_requested"

    PAYMENT_CREATED = "payment.created"
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_RECEIVED = "payment.received"
    PAYMENT_REFUNDED = "payment.refunded"

    REFUND_REQUESTED = "refund.requested"
    REFUND_APPROVED = "refund.approved"
    REFUND_REJECTED = "refund.rejected"
    REFUND_COMPLETED = "refund.completed"

    CREDIT_NOTE_CREATED = "credit_note.created"
    CREDIT_NOTE_APPROVED = "credit_note.approved"
    CREDIT_NOTE_APPLIED = "credit_note.applied"

    WRITEOFF_REQUESTED = "writeoff.requested"
    WRITEOFF_APPROVED = "writeoff.approved"
    WRITEOFF_APPLIED = "writeoff.applied"

    JOB_COST_RECORDED = "job.cost_recorded"
    JOB_MARGIN_UPDATED = "job.margin_updated"

    CASH_FORECAST_UPDATED = "cash_forecast.updated"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXECUTION_STARTED = "approval.execution.started"
    APPROVAL_EXECUTION_COMPLETED = "approval.execution.completed"
    APPROVAL_EXECUTION_FAILED = "approval.execution.failed"

    INTEGRATION_FAILED = "integration.failed"
    # Phase 12C
    INTEGRATION_CONNECTED = "integration.connected"
    INTEGRATION_DISCONNECTED = "integration.disconnected"
    INTEGRATION_CONNECTION_FAILED = "integration.connection_failed"
    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_REJECTED = "webhook.rejected"

    # Marketing (Phase 6)
    MARKETING_CAMPAIGN_CREATED = "marketing.campaign_created"
    MARKETING_SPEND_RECORDED = "marketing.spend_recorded"
    MARKETING_LEAD_ATTRIBUTED = "marketing.lead_attributed"
    MARKETING_CONTENT_CREATED = "marketing.content_created"
    MARKETING_CONTENT_APPROVED = "marketing.content_approved"
    MARKETING_CONTENT_PUBLISHED = "marketing.content_published"
    MARKETING_LEAD_ADDED_TO_SEQUENCE = "marketing.lead_added_to_sequence"
    MARKETING_NURTURE_STARTED = "marketing.nurture_started"
    MARKETING_REACTIVATION_CANDIDATE_CREATED = "marketing.reactivation_candidate_created"
    MARKETING_REACTIVATION_SENT = "marketing.reactivation_sent"
    MARKETING_CAMPAIGN_PERFORMANCE_UPDATED = "marketing.campaign_performance_updated"

    # Retention & Referral (Phase 7)
    RETENTION_LIFECYCLE_CHANGED = "retention.customer_lifecycle_changed"
    RETENTION_OPPORTUNITY_CREATED = "retention.opportunity_created"
    RETENTION_REMINDER_CREATED = "retention.reminder_created"
    RETENTION_REMINDER_DUE = "retention.reminder_due"
    RETENTION_REVIEW_REQUEST_CREATED = "retention.review_request_created"
    RETENTION_REVIEW_RECEIVED = "retention.review_received"
    RETENTION_FEEDBACK_RECEIVED = "retention.feedback_received"
    RETENTION_SERVICE_RECOVERY_REQUIRED = "retention.service_recovery_required"
    RETENTION_REFERRAL_CREATED = "retention.referral_created"
    RETENTION_REFERRAL_LEAD_CREATED = "retention.referral_lead_created"
    RETENTION_REFERRAL_QUALIFIED = "retention.referral_qualified"
    RETENTION_REFERRAL_BOOKED = "retention.referral_booked"
    RETENTION_REFERRAL_CONVERTED = "retention.referral_converted"
    RETENTION_REWARD_REQUESTED = "retention.reward_requested"
    RETENTION_REWARD_APPROVED = "retention.reward_approved"
    RETENTION_REWARD_ISSUED = "retention.reward_issued"

    # Morning Brief (Phase 10B): published once per real generation that
    # produced at least one recommendation — so a notification handler can
    # create ONE grouped "N actions need attention" notification instead of
    # the AI-execution-service audit rows (which fire per snapshot tool
    # call, not per brief) doing it.
    MORNING_BRIEF_GENERATED = "morning_brief.generated"


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
    # Added Phase 8 (event worker observability) — nullable so existing rows
    # written before this column existed remain valid.
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
