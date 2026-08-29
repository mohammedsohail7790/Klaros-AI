"""Phase 7: Retention & Referral.

Money (`ReferralReward.amount`) is `Decimal`/`Numeric`, same convention as
`app/models/finance.py`/`marketing.py` — never `float`.

Deliberately NOT persisted here: a "customer service history" table and a
"customer value snapshot" table. Both are fully derivable on request from
real `Job`/`Invoice`/`Payment`/`CommunicationLog`/`ReviewRequest`/
`Referral` rows — see `app/services/retention_service.py`'s
`customer_service_history()` — the same "derived service, never a stale
cache" decision Phase 5 made for AR and Phase 6 made for campaign
performance. `Customer`, `Lead`, `Job`, `Invoice`, `Payment`,
`CommunicationLog`, `OperationsException`, `Campaign`, `CampaignLead`,
`CampaignConversion` are reused, never duplicated.

Referral attribution deliberately reuses the Phase 6 attribution engine
rather than building a second one: `ReferralProgram.campaign_id` points at
a real `Campaign` row (channel=REFERRAL), so `AttributionService.
campaign_performance` already reports referral spend/leads/revenue/CAC/
ROAS with zero new analytics code. `Referral.status` tracks the
referral-specific lifecycle (CREATED..REWARDED) in parallel, driven by the
same real events the `CampaignConversion` stage already reacts to.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


# --- Customer lifecycle -------------------------------------------------------------


class LifecycleState(StrEnum):
    NEW = "NEW"
    FIRST_SERVICE = "FIRST_SERVICE"
    ACTIVE = "ACTIVE"
    REPEAT_CUSTOMER = "REPEAT_CUSTOMER"
    AT_RISK = "AT_RISK"
    INACTIVE = "INACTIVE"
    REACTIVATED = "REACTIVATED"
    ADVOCATE = "ADVOCATE"


class CustomerLifecycleProfile(TenantScopedMixin, Base):
    """One row per customer — the current lifecycle state plus the real
    counters that justified the last transition. Never a fabricated
    transition: every state change here is driven by a real `job.closed`/
    `payment.received` event or a deterministic inactivity check."""

    __tablename__ = "customer_lifecycle_profiles"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(20), nullable=False, default=LifecycleState.NEW, index=True)
    jobs_completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_service_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_service_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "customer_id", name="uq_lifecycle_profiles_tenant_customer"),
    )


# --- Retention opportunities -------------------------------------------------------------


class OpportunityType(StrEnum):
    POST_JOB_FOLLOWUP = "POST_JOB_FOLLOWUP"
    SERVICE_REMINDER_DUE = "SERVICE_REMINDER_DUE"
    WIN_BACK = "WIN_BACK"
    REVIEW_ELIGIBLE = "REVIEW_ELIGIBLE"
    REFERRAL_ELIGIBLE = "REFERRAL_ELIGIBLE"
    SERVICE_RECOVERY = "SERVICE_RECOVERY"


class OpportunityStatus(StrEnum):
    OPEN = "OPEN"
    CONTACTED = "CONTACTED"
    CONVERTED = "CONVERTED"
    DISMISSED = "DISMISSED"
    EXPIRED = "EXPIRED"


class RetentionOpportunity(TenantScopedMixin, Base):
    __tablename__ = "retention_opportunities"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OpportunityStatus.OPEN, index=True)
    source_event: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "type", "status",
            name="uq_retention_opportunities_tenant_customer_type_open",
        ),
    )


# --- Service reminders -------------------------------------------------------------


class ReminderStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    DUE = "DUE"
    SENT = "SENT"
    RESPONDED = "RESPONDED"
    BOOKED = "BOOKED"
    CANCELLED = "CANCELLED"


class ServiceReminder(TenantScopedMixin, Base):
    """Deterministic `reminder_date` + on-demand execution — same safe
    pattern as Phase 5's `CollectionAction`/Phase 6's `OutboundActivity`.
    No `workflow.sleep()`."""

    __tablename__ = "service_reminders"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    service_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reminder_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReminderStatus.SCHEDULED)


# --- Reviews & feedback -------------------------------------------------------------


class ReviewStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REQUESTED = "REQUESTED"
    RESPONDED = "RESPONDED"
    RECEIVED = "RECEIVED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class ReviewChannel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    WEB = "WEB"
    INTERNAL = "INTERNAL"


class ReviewRequest(TenantScopedMixin, Base):
    __tablename__ = "review_requests"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default=ReviewChannel.INTERNAL)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReviewStatus.ELIGIBLE)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "job_id", name="uq_review_requests_tenant_job"),
    )


class FeedbackSentiment(StrEnum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class CustomerFeedback(TenantScopedMixin, Base):
    """`sentiment` is always derived deterministically from `rating` (1-2
    negative, 3 neutral, 4-5 positive) at write time — never an AI guess."""

    __tablename__ = "customer_feedback"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- Referral system -------------------------------------------------------------


class ReferralProgramStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class ReferralProgram(TenantScopedMixin, Base):
    """`campaign_id` points at a real `Campaign` row (channel=REFERRAL) —
    referral performance rides the *existing* Phase 6 attribution engine,
    not a second one."""

    __tablename__ = "referral_programs"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    reward_type: Mapped[str] = mapped_column(String(50), nullable=False, default="credit")
    reward_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReferralProgramStatus.ACTIVE)


class ReferralCode(TenantScopedMixin, Base):
    __tablename__ = "referral_codes"

    program_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_referral_codes_tenant_code"),
        UniqueConstraint("tenant_id", "program_id", "customer_id", name="uq_referral_codes_tenant_program_customer"),
    )


class ReferralStatus(StrEnum):
    CREATED = "CREATED"
    CLICKED = "CLICKED"
    LEAD_CREATED = "LEAD_CREATED"
    QUALIFIED = "QUALIFIED"
    BOOKED = "BOOKED"
    CONVERTED = "CONVERTED"
    REWARDED = "REWARDED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class Referral(TenantScopedMixin, Base):
    """One row per referral. `lead_id` is the *existing* `Lead` created
    from this referral — never a second lead record. `revenue_amount`/
    `collected_amount` mirror `CampaignConversion`'s pattern, scoped to
    this one referral, so `/retention/referrals` can show real numbers
    without re-deriving them from the campaign every time."""

    __tablename__ = "referrals"

    program_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    referral_code_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    referrer_customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    referred_customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReferralStatus.CREATED, index=True)
    revenue_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    collected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    created_at_referral: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "lead_id", name="uq_referrals_tenant_lead"),
    )


class RewardStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"


class ReferralReward(TenantScopedMixin, Base):
    """Financial rewards always start `PENDING` and only ever move to
    `ISSUED` via a dedicated, permission-gated `approve_referral_reward`
    tool that resolves the `ApprovalRequest` *and* completes the issuance
    in one call — avoiding the generic "approval doesn't resume the
    action" dead end named in Phase 5/6. No real payment provider is ever
    called; issuance is an internal record only."""

    __tablename__ = "referral_rewards"

    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RewardStatus.PENDING)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "referral_id", name="uq_referral_rewards_tenant_referral"),
    )


# --- Risk & advocacy -------------------------------------------------------------


class RiskSignalType(StrEnum):
    INACTIVITY = "INACTIVITY"
    NEGATIVE_FEEDBACK = "NEGATIVE_FEEDBACK"
    UNRESOLVED_EXCEPTION = "UNRESOLVED_EXCEPTION"
    PAYMENT_ISSUE = "PAYMENT_ISSUE"
    MISSED_APPOINTMENT = "MISSED_APPOINTMENT"
    SERVICE_GAP = "SERVICE_GAP"


class RiskSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CustomerRiskSignal(TenantScopedMixin, Base):
    __tablename__ = "customer_risk_signals"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default=RiskSeverity.LOW)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "signal_type", "resolved",
            name="uq_customer_risk_signals_tenant_customer_type_open",
        ),
    )


class AdvocateCandidateStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class AdvocateCandidate(TenantScopedMixin, Base):
    """Deterministic signals only — see
    `app/services/retention_service.py`'s selection rules (repeat jobs, no
    unresolved complaint, paid invoices, positive feedback, referrals)."""

    __tablename__ = "advocate_candidates"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    signals: Mapped[str] = mapped_column(Text, nullable=False)  # comma-separated real signal names
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AdvocateCandidateStatus.PENDING)
    identified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "customer_id", "status", name="uq_advocate_candidates_tenant_customer_pending"),
    )


# --- Retention campaigns -------------------------------------------------------------


class RetentionCampaignType(StrEnum):
    POST_JOB_FOLLOWUP = "POST_JOB_FOLLOWUP"
    SERVICE_REMINDER = "SERVICE_REMINDER"
    WIN_BACK = "WIN_BACK"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    REFERRAL_INVITE = "REFERRAL_INVITE"
    VIP_CUSTOMER = "VIP_CUSTOMER"
    SERVICE_RECOVERY = "SERVICE_RECOVERY"


class RetentionCampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RetentionCampaign(TenantScopedMixin, Base):
    __tablename__ = "retention_campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RetentionCampaignStatus.DRAFT)


class RetentionEnrollmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class RetentionEnrollment(TenantScopedMixin, Base):
    __tablename__ = "retention_enrollments"

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RetentionEnrollmentStatus.ACTIVE)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "campaign_id", "customer_id", name="uq_retention_enroll_tenant_campaign_customer"),
    )


class RetentionActivityStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class RetentionActivity(TenantScopedMixin, Base):
    __tablename__ = "retention_activities"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default="EMAIL")
    template: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RetentionActivityStatus.PENDING)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
