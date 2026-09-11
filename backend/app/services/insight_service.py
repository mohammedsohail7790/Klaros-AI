"""Phase 8B: read-only aggregation for the Morning Brief's `insights.*`
tools (see app/tools/builtin/insight_tools.py). Each method here answers one
narrow, decision-relevant question from real rows — deliberately smaller
than the full `/finance/summary`, `/marketing/summary`, etc. dashboard
endpoints (which stay untouched), not a replacement for them. Insufficient
data is returned as `None`/empty, never fabricated.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.models.contract import Contract, ContractStatus
from app.models.crm import Appointment, AppointmentStatus, Lead, LeadStatus, QualificationStatus
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.models.quote import Quote, QuoteStatus
from app.models.marketing import Campaign, CampaignStatus, MarketingContent, MarketingSpend
from app.models.operations import ExceptionStatus, Job, JobStatus, OperationsException
from app.models.voice import CallOutcome, CallSession
from app.models.retention import (
    CustomerFeedback,
    CustomerLifecycleProfile,
    CustomerRiskSignal,
    FeedbackSentiment,
    LifecycleState,
    OpportunityStatus,
    ReferralReward,
    RetentionOpportunity,
    ReviewRequest,
    ReviewStatus,
    RewardStatus,
)


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    day_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    return day_start, day_start + timedelta(days=1)


@dataclass
class FinanceSnapshot:
    total_ar: Decimal
    overdue_invoice_count: int
    pending_approval_count: int
    collected_last_24h: Decimal
    payments_last_24h_count: int


@dataclass
class CommercialPipelineSnapshot:
    contracts_awaiting_signature: list[dict]
    deposits_awaiting_payment: list[dict]
    stale_quotes_awaiting_response: list[dict]


@dataclass
class OperationsSnapshot:
    jobs_closed_last_24h: int
    blocked_jobs_count: int
    unassigned_scheduled_jobs_count: int
    open_exceptions_count: int


@dataclass
class SalesSnapshot:
    new_leads_today: int
    qualified_leads_today: int
    appointments_today: int
    qualified_leads_awaiting_appointment: list[dict]


@dataclass
class MarketingSnapshot:
    active_campaign_count: int
    spend_last_30d: Decimal
    leads_last_24h: int


@dataclass
class RetentionSnapshot:
    at_risk_customers: int
    open_retention_opportunities: int
    open_retention_opportunities_detail: list[dict]
    eligible_review_requests: list[dict]
    reviews_awaiting_marketing_consent: list[dict]
    reviews_ready_for_marketing_content: list[dict]
    negative_feedback_last_24h: list[dict]
    pending_referral_rewards: list[dict]


@dataclass
class ExceptionSnapshot:
    open_count: int
    high_severity_count: int
    top_open: list[dict]


@dataclass
class VoiceSnapshot:
    calls_today: int
    new_leads_from_voice_today: int
    human_handoffs_today: int
    unresolved_calls_today: int
    recent_calls: list[dict]


class InsightService:
    """Every method is tenant-scoped by an explicit `tenant_id` argument —
    callers (the `insights.*` tools) always pass `context.tenant_id`, never
    a client-supplied value, matching the tenant-isolation rule used
    everywhere else in this codebase."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def finance_snapshot(self, tenant_id: uuid.UUID) -> FinanceSnapshot:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        async with self._session_factory() as session:
            open_statuses = [InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE]
            total_ar = (
                await session.execute(
                    select(func.coalesce(func.sum(Invoice.amount_due), 0)).where(
                        Invoice.tenant_id == tenant_id, Invoice.status.in_(open_statuses)
                    )
                )
            ).scalar_one()
            overdue_count = (
                await session.execute(
                    select(func.count()).where(
                        Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.OVERDUE
                    )
                )
            ).scalar_one()
            pending_approval_count = (
                await session.execute(
                    select(func.count()).where(
                        Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.PENDING_APPROVAL
                    )
                )
            ).scalar_one()
            collected = (
                await session.execute(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.tenant_id == tenant_id, Payment.received_at >= since
                    )
                )
            ).scalar_one()
            payments_count = (
                await session.execute(
                    select(func.count()).where(Payment.tenant_id == tenant_id, Payment.received_at >= since)
                )
            ).scalar_one()
            return FinanceSnapshot(
                total_ar=Decimal(total_ar),
                overdue_invoice_count=overdue_count,
                pending_approval_count=pending_approval_count,
                collected_last_24h=Decimal(collected),
                payments_last_24h_count=payments_count,
            )

    async def operations_snapshot(self, tenant_id: uuid.UUID) -> OperationsSnapshot:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        async with self._session_factory() as session:
            closed_count = (
                await session.execute(
                    select(func.count()).where(
                        Job.tenant_id == tenant_id,
                        Job.status == JobStatus.CLOSED,
                        Job.updated_at >= since,
                    )
                )
            ).scalar_one()
            blocked_count = (
                await session.execute(
                    select(func.count()).where(Job.tenant_id == tenant_id, Job.status == JobStatus.BLOCKED)
                )
            ).scalar_one()
            unassigned_count = (
                await session.execute(
                    select(func.count()).where(
                        Job.tenant_id == tenant_id,
                        Job.status == JobStatus.SCHEDULED,
                        Job.assigned_user_id.is_(None),
                    )
                )
            ).scalar_one()
            open_exceptions = (
                await session.execute(
                    select(func.count()).where(
                        OperationsException.tenant_id == tenant_id,
                        OperationsException.status == ExceptionStatus.OPEN,
                    )
                )
            ).scalar_one()
            return OperationsSnapshot(
                jobs_closed_last_24h=closed_count,
                blocked_jobs_count=blocked_count,
                unassigned_scheduled_jobs_count=unassigned_count,
                open_exceptions_count=open_exceptions,
            )

    async def sales_snapshot(self, tenant_id: uuid.UUID) -> SalesSnapshot:
        now = datetime.now(timezone.utc)
        day_start, day_end = _day_bounds(now)
        async with self._session_factory() as session:
            new_leads = (
                await session.execute(
                    select(func.count()).where(
                        Lead.tenant_id == tenant_id, Lead.created_at >= day_start, Lead.created_at < day_end
                    )
                )
            ).scalar_one()
            qualified_today = (
                await session.execute(
                    select(func.count()).where(
                        Lead.tenant_id == tenant_id,
                        Lead.qualification_status == QualificationStatus.QUALIFIED,
                        Lead.updated_at >= day_start,
                        Lead.updated_at < day_end,
                    )
                )
            ).scalar_one()
            appointments_today = (
                await session.execute(
                    select(func.count()).where(
                        Appointment.tenant_id == tenant_id,
                        Appointment.start_time >= day_start,
                        Appointment.start_time < day_end,
                        Appointment.status != AppointmentStatus.CANCELLED,
                    )
                )
            ).scalar_one()

            leads_with_appointment = set(
                (
                    await session.execute(
                        select(Appointment.lead_id).where(
                            Appointment.tenant_id == tenant_id, Appointment.lead_id.is_not(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            qualified_leads = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == tenant_id,
                        Lead.qualification_status == QualificationStatus.QUALIFIED,
                        Lead.status.not_in([LeadStatus.LOST, LeadStatus.CONVERTED]),
                    )
                )
            ).scalars().all()
            awaiting = [
                {
                    "lead_id": str(lead.id),
                    "name": lead.name,
                    "qualified_days_ago": (now - lead.updated_at.replace(tzinfo=timezone.utc)).days
                    if lead.updated_at.tzinfo is None
                    else (now - lead.updated_at).days,
                }
                for lead in qualified_leads
                if lead.id not in leads_with_appointment
            ]
            return SalesSnapshot(
                new_leads_today=new_leads,
                qualified_leads_today=qualified_today,
                appointments_today=appointments_today,
                qualified_leads_awaiting_appointment=awaiting,
            )

    async def commercial_pipeline_snapshot(self, tenant_id: uuid.UUID) -> CommercialPipelineSnapshot:
        """The Quote -> Contract -> Deposit boundary — distinct from
        `finance_snapshot` (Invoice/AR only) and `sales_snapshot` (Lead/
        Appointment only). Real, entity-level detail (not just counts) so
        the Morning Brief's recommendations can link to the specific
        contract/quote that needs attention, matching every other
        recommendation's `related_entity_id` traceability."""
        async with self._session_factory() as session:
            pending_contracts = (
                await session.execute(
                    select(Contract).where(
                        Contract.tenant_id == tenant_id,
                        Contract.status.in_((ContractStatus.SENT, ContractStatus.VIEWED)),
                    )
                )
            ).scalars().all()
            contracts_awaiting_signature = [
                {"contract_id": str(c.id), "contract_number": c.contract_number, "status": c.status}
                for c in pending_contracts
            ]

            pending_deposits = (
                await session.execute(
                    select(Quote).where(Quote.tenant_id == tenant_id, Quote.status == QuoteStatus.DEPOSIT_PENDING)
                )
            ).scalars().all()
            deposits_awaiting_payment = [
                {
                    "quote_id": str(q.id), "quote_number": q.quote_number,
                    "deposit_amount": str(q.deposit_amount) if q.deposit_amount is not None else None,
                }
                for q in pending_deposits
            ]

            settings = get_settings()
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.STALE_QUOTE_FOLLOWUP_DAYS)
            stale_quote_rows = (
                await session.execute(
                    select(Quote).where(
                        Quote.tenant_id == tenant_id,
                        Quote.status.in_((QuoteStatus.SENT, QuoteStatus.VIEWED)),
                        Quote.sent_at.is_not(None),
                        Quote.sent_at <= stale_cutoff,
                    )
                )
            ).scalars().all()
            stale_quotes_awaiting_response = [
                {
                    "quote_id": str(q.id), "quote_number": q.quote_number, "total": str(q.total),
                    "sent_at": q.sent_at.isoformat() if q.sent_at else None,
                }
                for q in stale_quote_rows
            ]

            return CommercialPipelineSnapshot(
                contracts_awaiting_signature=contracts_awaiting_signature,
                deposits_awaiting_payment=deposits_awaiting_payment,
                stale_quotes_awaiting_response=stale_quotes_awaiting_response,
            )

    async def marketing_snapshot(self, tenant_id: uuid.UUID) -> MarketingSnapshot:
        now = datetime.now(timezone.utc)
        since_24h = now - timedelta(hours=24)
        since_30d = (now - timedelta(days=30)).date()
        async with self._session_factory() as session:
            active_campaigns = (
                await session.execute(
                    select(func.count()).where(
                        Campaign.tenant_id == tenant_id, Campaign.status == CampaignStatus.ACTIVE
                    )
                )
            ).scalar_one()
            spend_30d = (
                await session.execute(
                    select(func.coalesce(func.sum(MarketingSpend.amount), 0)).where(
                        MarketingSpend.tenant_id == tenant_id, MarketingSpend.spend_date >= since_30d
                    )
                )
            ).scalar_one()
            leads_24h = (
                await session.execute(
                    select(func.count()).where(Lead.tenant_id == tenant_id, Lead.created_at >= since_24h)
                )
            ).scalar_one()
            return MarketingSnapshot(
                active_campaign_count=active_campaigns,
                spend_last_30d=Decimal(spend_30d),
                leads_last_24h=leads_24h,
            )

    async def retention_snapshot(self, tenant_id: uuid.UUID) -> RetentionSnapshot:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        async with self._session_factory() as session:
            at_risk = (
                await session.execute(
                    select(func.count()).where(
                        CustomerLifecycleProfile.tenant_id == tenant_id,
                        CustomerLifecycleProfile.lifecycle_state == LifecycleState.AT_RISK,
                    )
                )
            ).scalar_one()
            open_opportunity_rows = (
                await session.execute(
                    select(RetentionOpportunity).where(
                        RetentionOpportunity.tenant_id == tenant_id,
                        RetentionOpportunity.status == OpportunityStatus.OPEN,
                    )
                )
            ).scalars().all()
            open_opportunities = len(open_opportunity_rows)
            eligible_review_rows = (
                await session.execute(
                    select(ReviewRequest).where(
                        ReviewRequest.tenant_id == tenant_id,
                        ReviewRequest.status == ReviewStatus.ELIGIBLE,
                    )
                )
            ).scalars().all()
            negative_feedback_rows = (
                await session.execute(
                    select(CustomerFeedback).where(
                        CustomerFeedback.tenant_id == tenant_id,
                        CustomerFeedback.sentiment == FeedbackSentiment.NEGATIVE,
                        CustomerFeedback.received_at >= since,
                    )
                )
            ).scalars().all()

            # Reviews/referral-conversion -> marketing content proof loop:
            # eligible (rating bar) positive feedback that hasn't already
            # become MarketingContent, split by whether consent has been
            # explicitly recorded yet. Never surfaces anything below the
            # deterministic eligibility bar, and never treats a missing
            # consent value as consent.
            settings = get_settings()
            already_content_ids = {
                row[0]
                for row in (
                    await session.execute(
                        select(MarketingContent.source_feedback_id).where(
                            MarketingContent.tenant_id == tenant_id,
                            MarketingContent.source_feedback_id.is_not(None),
                        )
                    )
                ).all()
            }
            eligible_feedback_rows = (
                await session.execute(
                    select(CustomerFeedback).where(
                        CustomerFeedback.tenant_id == tenant_id,
                        CustomerFeedback.sentiment == FeedbackSentiment.POSITIVE,
                        CustomerFeedback.rating >= settings.REVIEW_MARKETING_MIN_RATING,
                    )
                )
            ).scalars().all()
            eligible_feedback_rows = [f for f in eligible_feedback_rows if f.id not in already_content_ids]
            reviews_awaiting_consent = [
                f for f in eligible_feedback_rows if f.consent_to_use_publicly is not True
            ]
            reviews_ready_for_content = [
                f for f in eligible_feedback_rows if f.consent_to_use_publicly is True
            ]
            pending_reward_rows = (
                await session.execute(
                    select(ReferralReward).where(
                        ReferralReward.tenant_id == tenant_id, ReferralReward.status == RewardStatus.PENDING
                    )
                )
            ).scalars().all()
            return RetentionSnapshot(
                at_risk_customers=at_risk,
                open_retention_opportunities=open_opportunities,
                open_retention_opportunities_detail=[
                    {
                        "opportunity_id": str(o.id),
                        "customer_id": str(o.customer_id),
                        "type": o.type,
                        "reason": o.reason,
                        "recommended_action": o.recommended_action,
                    }
                    for o in open_opportunity_rows[:5]
                ],
                eligible_review_requests=[
                    {"review_request_id": str(r.id), "customer_id": str(r.customer_id), "job_id": str(r.job_id) if r.job_id else None}
                    for r in eligible_review_rows[:5]
                ],
                reviews_awaiting_marketing_consent=[
                    {"feedback_id": str(f.id), "customer_id": str(f.customer_id), "rating": f.rating}
                    for f in reviews_awaiting_consent[:5]
                ],
                reviews_ready_for_marketing_content=[
                    {"feedback_id": str(f.id), "customer_id": str(f.customer_id), "rating": f.rating}
                    for f in reviews_ready_for_content[:5]
                ],
                negative_feedback_last_24h=[
                    {
                        "feedback_id": str(f.id),
                        "customer_id": str(f.customer_id),
                        "rating": f.rating,
                        "comment": f.comment,
                    }
                    for f in negative_feedback_rows
                ],
                pending_referral_rewards=[
                    {"reward_id": str(r.id), "customer_id": str(r.customer_id), "amount": str(r.amount)}
                    for r in pending_reward_rows
                ],
            )

    async def exception_snapshot(self, tenant_id: uuid.UUID) -> ExceptionSnapshot:
        async with self._session_factory() as session:
            open_rows = (
                await session.execute(
                    select(OperationsException)
                    .where(OperationsException.tenant_id == tenant_id, OperationsException.status == ExceptionStatus.OPEN)
                    .order_by(OperationsException.created_at.desc())
                )
            ).scalars().all()
            high = [e for e in open_rows if e.severity == "HIGH"]
            top = open_rows[:10]
            return ExceptionSnapshot(
                open_count=len(open_rows),
                high_severity_count=len(high),
                top_open=[
                    {
                        "exception_id": str(e.id),
                        "type": e.type,
                        "severity": e.severity,
                        "description": e.description,
                        "entity_type": e.entity_type,
                        "entity_id": str(e.entity_id),
                    }
                    for e in top
                ],
            )

    async def voice_snapshot(self, tenant_id: uuid.UUID) -> VoiceSnapshot:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        async with self._session_factory() as session:
            calls = (
                await session.execute(
                    select(CallSession)
                    .where(CallSession.tenant_id == tenant_id, CallSession.started_at >= since)
                    .order_by(CallSession.started_at.desc())
                )
            ).scalars().all()
            new_leads = sum(1 for c in calls if c.lead_id is not None)
            handoffs = sum(1 for c in calls if c.handoff_requested)
            unresolved = sum(1 for c in calls if c.outcome in (CallOutcome.UNRESOLVED, CallOutcome.PROVIDER_FAILURE, CallOutcome.AI_FAILURE))
            return VoiceSnapshot(
                calls_today=len(calls),
                new_leads_from_voice_today=new_leads,
                human_handoffs_today=handoffs,
                unresolved_calls_today=unresolved,
                recent_calls=[
                    {
                        "call_id": str(c.id), "caller_number": c.caller_number, "status": c.status,
                        "outcome": c.outcome, "started_at": c.started_at.isoformat(),
                        "handoff_requested": c.handoff_requested,
                    }
                    for c in calls[:10]
                ],
            )
