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

from app.models.crm import Appointment, AppointmentStatus, Lead, LeadStatus, QualificationStatus
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.models.marketing import Campaign, CampaignStatus, MarketingSpend
from app.models.operations import ExceptionStatus, Job, JobStatus, OperationsException
from app.models.retention import (
    CustomerFeedback,
    CustomerLifecycleProfile,
    CustomerRiskSignal,
    FeedbackSentiment,
    LifecycleState,
    OpportunityStatus,
    ReferralReward,
    RetentionOpportunity,
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
    negative_feedback_last_24h: list[dict]
    pending_referral_rewards: list[dict]


@dataclass
class ExceptionSnapshot:
    open_count: int
    high_severity_count: int
    top_open: list[dict]


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
            open_opportunities = (
                await session.execute(
                    select(func.count()).where(
                        RetentionOpportunity.tenant_id == tenant_id,
                        RetentionOpportunity.status == OpportunityStatus.OPEN,
                    )
                )
            ).scalar_one()
            negative_feedback_rows = (
                await session.execute(
                    select(CustomerFeedback).where(
                        CustomerFeedback.tenant_id == tenant_id,
                        CustomerFeedback.sentiment == FeedbackSentiment.NEGATIVE,
                        CustomerFeedback.received_at >= since,
                    )
                )
            ).scalars().all()
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
