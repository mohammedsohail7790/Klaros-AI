"""Clinic outreach campaigns: which clinics a campaign is working through, where each one is in the pipeline, and who must never be contacted. Phase 1 stores state only; nothing here sends email."""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class OutreachMode(StrEnum):
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class OutreachStage(StrEnum):
    QUEUED = "QUEUED"
    NO_EMAIL = "NO_EMAIL"
    READY = "READY"
    INTRO_SENT = "INTRO_SENT"
    FOLLOWUP_1 = "FOLLOWUP_1"
    FOLLOWUP_2 = "FOLLOWUP_2"
    NO_RESPONSE = "NO_RESPONSE"
    REPLIED = "REPLIED"
    TERMS_PROPOSED = "TERMS_PROPOSED"
    NEEDS_PERSON = "NEEDS_PERSON"
    AGREED_PENDING = "AGREED_PENDING"
    DECLINED = "DECLINED"
    OPTED_OUT = "OPTED_OUT"
    HANDED_TO_VERIFICATION = "HANDED_TO_VERIFICATION"


class OutreachMessageKind(StrEnum):
    INTRO = "intro"
    FOLLOWUP_1 = "followup_1"
    FOLLOWUP_2 = "followup_2"


class OutreachMessageStatus(StrEnum):
    QUEUED_SHADOW = "QUEUED_SHADOW"
    BLOCKED = "BLOCKED"
    QUEUED_LIVE = "QUEUED_LIVE"  # drafted in a live campaign, waiting for its time to be sent
    SENT = "SENT"
    FAILED = "FAILED"  # two send attempts failed
    CANCELLED = "CANCELLED"  # a guard stopped it at send time (suppressed, clinic replied, handed to a person)
    SHADOW_ARCHIVED = "SHADOW_ARCHIVED"  # a shadow draft kept for history after Go live; never sent, ignored by the engine


class ClinicOutreachCampaign(TenantScopedMixin, Base):
    __tablename__ = "clinic_outreach_campaigns"

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("campaigns.id"), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default=OutreachMode.SHADOW)
    term_sheet: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sender: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    limits: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    countries: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shadow_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_checks: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    engine_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClinicOutreach(TenantScopedMixin, Base):
    __tablename__ = "clinic_outreach"
    __table_args__ = (UniqueConstraint("campaign_id", "provider_id", name="uq_clinic_outreach_campaign_provider"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("campaigns.id"), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default=OutreachStage.QUEUED, index=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_source: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "owner" | "site"
    contact_source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    contact_found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contact_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # every discovery attempt, found or not
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emails_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OutreachSuppression(TenantScopedMixin, Base):
    """Addresses and domains this tenant must never contact."""

    __tablename__ = "outreach_suppressions"
    __table_args__ = (UniqueConstraint("tenant_id", "value", name="uq_outreach_suppression_value"),)

    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # "address" | "domain"
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ClinicOutreachMessage(TenantScopedMixin, Base):
    """A drafted outreach email held in shadow (never sent in this phase). The engine must set `schedule_day` to the UTC date of `scheduled_for`; the unique constraint then allows one message per clinic, kind and day."""

    __tablename__ = "clinic_outreach_messages"
    __table_args__ = (
        UniqueConstraint("outreach_id", "kind", "schedule_day", name="uq_outreach_message_kind_day"),
        Index("ix_outreach_messages_tenant_status_scheduled", "tenant_id", "status", "scheduled_for"),
    )

    outreach_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("clinic_outreach.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False, default="OUT")
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    blocks_version: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schedule_day: Mapped[date] = mapped_column(Date, nullable=False)  # UTC date of scheduled_for, set by the writer
    review_mark: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "GOOD" | "FLAGGED"
    review_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
