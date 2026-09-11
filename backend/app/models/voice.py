"""Phase 4: the AI Voice Receptionist's own domain state — a `CallSession`
per inbound call and one `VoiceSettings` row per tenant. Deliberately does
NOT duplicate CRM/appointment/knowledge state: a call that produces a lead
still creates that lead through the canonical `LeadService`/`crm.create_lead`
path (see app/services/voice_conversation_service.py) — `CallSession` only
tracks the call itself (who called, what happened, what it produced),
linking out to those real records by id.

Raw audio is never persisted here — only a bounded text transcript
(caller turns + agent replies), which is real content the business needs
for quality/dispute review, not indefinite raw recording storage.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class CallDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class CallStatus(StrEnum):
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BookingState(StrEnum):
    """Phase 5: the deterministic appointment-booking sub-state-machine,
    persisted in `CallSession.engine_state["booking"]["state"]` — never
    held only in memory, so a call can be reasoned about (and, if the
    process restarts mid-call, recovered) purely from the database."""

    IDLE = "IDLE"
    COLLECTING_CUSTOMER_INFO = "COLLECTING_CUSTOMER_INFO"
    COLLECTING_SERVICE_DETAILS = "COLLECTING_SERVICE_DETAILS"
    OFFERING_SLOTS = "OFFERING_SLOTS"
    CONFIRMING_APPOINTMENT = "CONFIRMING_APPOINTMENT"
    APPOINTMENT_CREATED = "APPOINTMENT_CREATED"
    HANDOFF_REQUIRED = "HANDOFF_REQUIRED"


class CallOutcome(StrEnum):
    NEW_LEAD_CREATED = "NEW_LEAD_CREATED"
    APPOINTMENT_BOOKED = "APPOINTMENT_BOOKED"
    EMERGENCY_ESCALATED = "EMERGENCY_ESCALATED"
    EXISTING_CUSTOMER_ASSISTED = "EXISTING_CUSTOMER_ASSISTED"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
    INFORMATION_PROVIDED = "INFORMATION_PROVIDED"
    UNRESOLVED = "UNRESOLVED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    AI_FAILURE = "AI_FAILURE"


class CallSession(TenantScopedMixin, Base):
    __tablename__ = "call_sessions"
    __table_args__ = (UniqueConstraint("provider", "external_call_id", name="uq_call_sessions_provider_external_id"),)

    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="twilio")
    external_call_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default=CallDirection.INBOUND)
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CallStatus.RINGING)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    handoff_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handoff_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Bounded conversation log: [{"role": "caller"|"agent", "text": "..."}]
    # — never raw audio, never unbounded (VoiceConversationService caps
    # turn count per call).
    transcript: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    # Engine-internal working memory (e.g. lead fields gathered so far,
    # proposed appointment slots awaiting confirmation) — distinct from
    # `transcript` (the human-readable call record); never audio, never
    # unbounded.
    engine_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class VoiceReceptionistSettings(TenantScopedMixin, Base):
    """One row per tenant — deliberately not folded into `Organization`
    (which is core auth/billing state, not feature configuration) nor into
    the Knowledge Layer (this is structured config, not a document)."""

    __tablename__ = "voice_receptionist_settings"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_voice_settings_tenant"),)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    greeting: Mapped[str] = mapped_column(
        Text, nullable=False, default="Thanks for calling. How can I help you today?"
    )
    business_hours_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
