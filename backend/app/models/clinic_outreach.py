"""Clinic outreach campaigns: which clinics a campaign is working through, where each one is in the pipeline, and who must never be contacted. Phase 1 stores state only; nothing here sends email."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
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
