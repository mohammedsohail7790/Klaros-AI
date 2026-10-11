"""Medical Tourism provider referrals: the patient-approved, provider-specific hand-over of a lead to a clinic, and the human work around it.

Four tenant-scoped tables (additive; nothing existing is changed by this module):

  ProviderReferral      one lead referred to one provider: status, the fields the patient agreed to share, send state, the clinic's response and its
                        reported outcome. A lead can be referred to several providers (one row each), never twice to the same one.
  ReferralApproval      APPEND-ONLY history of the patient's decision to share with ONE provider (granted / declined / withdrawn), who recorded it, the
                        wording version, the fields agreed and when. The newest row per (lead, provider) is the current decision; a withdrawal is a new
                        row, never an update.
  ReferralDelivery      one row per transfer of referral data to a provider (idempotency key unique): which fields went, through which transport, and
                        when. The payload is kept so an auditor can see exactly what left, and is scrubbed if the lead's personal data is erased.
  HandoffTask           a human task with an owner, a status and an audit trail (accept, complete, cancel), created when Halla hands a lead to a person.

Why a person records the approval: Halla never asks for, records or sends anything to a clinic. The coordinator records the patient's OWN decision
(`patient_informed=True` is required for a grant: the patient was told which clinic receives which information); they never grant it for them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin

# The identifiable lead information a patient may agree to share with ONE clinic. Anything not listed can never be shared. Health information is
# listed separately because it additionally needs `store_medical_information`.
SHAREABLE_FIELDS = ("name", "phone", "email", "preferred_language", "preferred_contact_time", "destination", "timeframe", "procedure")
MEDICAL_FIELDS = ("procedure",)
SHARE_SCOPE = "share_with_selected_provider"


class ReferralStatus(StrEnum):
    DRAFT = "DRAFT"  # provider selected; no approval recorded yet
    APPROVED = "APPROVED"  # the patient's approval for THIS provider is on record; nothing has been sent
    SENT = "SENT"  # permitted data was transferred
    ACCEPTED = "ACCEPTED"  # the clinic accepted the enquiry
    DECLINED = "DECLINED"  # the clinic declined it
    OUTCOME_REPORTED = "OUTCOME_REPORTED"  # the clinic reported an outcome
    CANCELLED = "CANCELLED"  # withdrawn / cancelled before or after sending (see approval history)


class ReferralOutcome(StrEnum):
    TREATMENT_COMPLETED = "TREATMENT_COMPLETED"
    CONSULTATION_ATTENDED = "CONSULTATION_ATTENDED"
    NO_TREATMENT = "NO_TREATMENT"
    PATIENT_DID_NOT_ATTEND = "PATIENT_DID_NOT_ATTEND"


class ApprovalDecision(StrEnum):
    GRANTED = "GRANTED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class ProviderReferral(TenantScopedMixin, Base):
    __tablename__ = "medical_tourism_provider_referrals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "lead_id", "provider_id", name="uq_mt_provider_referrals_tenant_lead_provider"),
        Index("ix_mt_provider_referrals_tenant_status", "tenant_id", "status"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False, index=True)
    procedure_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_procedures.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=ReferralStatus.DRAFT)
    permitted_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # copied from the current granted approval; never wider
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)  # the approval row this referral currently relies on
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_block_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)  # a closed code, never text from the patient
    clinic_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clinic_response_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)  # the staff member who recorded what the clinic said
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)  # where the clinic reported it (a reference, not clinical detail)


class ReferralApproval(TenantScopedMixin, Base):
    __tablename__ = "medical_tourism_referral_approvals"
    __table_args__ = (Index("ix_mt_referral_approvals_lead_provider", "tenant_id", "lead_id", "provider_id", "recorded_at"),)

    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_providers.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    permitted_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    patient_informed: Mapped[bool] = mapped_column(Boolean, nullable=False)  # the patient was told which clinic gets which information
    wording_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="coordinator_recorded")  # how the decision reached us (never "ai" / "system")
    channel: Mapped[str | None] = mapped_column(String(24), nullable=True)  # phone | email | in_person | other
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralDelivery(TenantScopedMixin, Base):
    __tablename__ = "medical_tourism_referral_deliveries"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_mt_referral_deliveries_tenant_key"),)

    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("medical_tourism_provider_referrals.id"), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    transport: Mapped[str] = mapped_column(String(24), nullable=False)
    fields_sent: Mapped[list] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # scrubbed (set to NULL) when the lead's personal data is erased
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class HandoffTask(TenantScopedMixin, Base):
    __tablename__ = "medical_tourism_handoff_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_mt_handoff_tasks_tenant_dedupe"),
        Index("ix_mt_handoff_tasks_tenant_status", "tenant_id", "status"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # a closed code: halla_handoff | escalation | provider_options | referral_followup
    reason_code: Mapped[str | None] = mapped_column(String(32), nullable=True)  # a closed code from the event, never free text
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=TaskStatus.OPEN)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)  # one task per source event and kind: a retried event never doubles it
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # written by a person; never filled from a call
