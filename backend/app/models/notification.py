"""Phase 8 introduced `Notification` as a bare in-app record. Phase 10
extends it in place (additive columns only) into the real owner-facing
notification the spec asks for: typed, prioritized, deduplicated,
entity-linked, and channel/status-tracked — while staying the ONE
notification table (`NotificationService`, app/services/notification_service.py,
is the only thing that writes to it)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class NotificationType(StrEnum):
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_FAILED = "ACTION_FAILED"
    HIGH_PRIORITY_EXCEPTION = "HIGH_PRIORITY_EXCEPTION"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    JOB_DELAYED = "JOB_DELAYED"
    NEGATIVE_FEEDBACK = "NEGATIVE_FEEDBACK"
    NEW_LEAD = "NEW_LEAD"
    MORNING_BRIEF_READY = "MORNING_BRIEF_READY"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    GENERAL = "GENERAL"  # pre-Phase-10 rows (crm_handlers.py appointment notice) land here


class NotificationPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class NotificationChannelName(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    READ = "READ"
    DISMISSED = "DISMISSED"


class Notification(TenantScopedMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("tenant_id", "dedupe_key", name="uq_notification_dedupe"),)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase 10 additions — all nullable/defaulted so every pre-existing row
    # (crm_handlers.py's appointment-booked notice) stays valid unchanged.
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False, default=NotificationType.GENERAL)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default=NotificationPriority.MEDIUM)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default=NotificationChannelName.IN_APP)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default=NotificationStatus.SENT)
    # NULL dedupe_key rows are never deduplicated (Postgres/SQLite both treat
    # NULL as distinct under a unique constraint) — only notifications that
    # opt in with a real key (e.g. "approval.requested:<approval_id>") get
    # the idempotency guarantee.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class NotificationPreference(TenantScopedMixin, Base):
    """Per-user, per-type, per-channel opt-in. No row for a given
    (user, type, channel) means "use the safe default" (see
    NotificationService.DEFAULT_PREFERENCES) — not "notifications
    disabled"."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "type", "channel", name="uq_notification_preference"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
