"""/retention dashboard summary + analytics — real, derived-on-request
numbers only, same honesty rule as Phase 5's /finance/summary and Phase
6's /marketing/summary. When a metric can't be reliably computed from
current data, the response says "INSUFFICIENT DATA" rather than inventing
a number."""

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.operations import ExceptionStatus, OperationsException
from app.models.retention import (
    CustomerLifecycleProfile,
    LifecycleState,
    Referral,
    ReferralReward,
    ReferralStatus,
    RetentionOpportunity,
    ServiceReminder,
    ReminderStatus,
    ReviewRequest,
    CustomerFeedback,
    OpportunityStatus,
)
from app.api.tool_deps import get_wired_event_bus
from app.services.attribution_service import AttributionService
from app.services.retention_service import RetentionService
from app.services.exception_service import ExceptionService
from app.events.bus import EventBus

router = APIRouter(prefix="/retention", tags=["retention"])

RETENTION_EXCEPTION_TYPES = (
    "CUSTOMER_AT_RISK", "SERVICE_RECOVERY_REQUIRED", "NEGATIVE_FEEDBACK", "MISSED_FOLLOWUP", "REFERRAL_REWARD_REVIEW",
)


@router.get("/summary")
async def retention_summary(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id = current_user.tenant_id
    async with async_session_maker() as session:
        profiles = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id))
        ).scalars().all()

        active = sum(1 for p in profiles if p.lifecycle_state in (LifecycleState.ACTIVE, LifecycleState.FIRST_SERVICE, LifecycleState.REPEAT_CUSTOMER, LifecycleState.ADVOCATE))
        repeat = sum(1 for p in profiles if p.lifecycle_state in (LifecycleState.REPEAT_CUSTOMER, LifecycleState.ADVOCATE))
        at_risk = sum(1 for p in profiles if p.lifecycle_state == LifecycleState.AT_RISK)
        inactive = sum(1 for p in profiles if p.lifecycle_state == LifecycleState.INACTIVE)

        open_opportunities = (
            await session.execute(
                select(func.count(RetentionOpportunity.id)).where(
                    RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.status == OpportunityStatus.OPEN,
                )
            )
        ).scalar_one()

        upcoming_reminders = (
            await session.execute(
                select(func.count(ServiceReminder.id)).where(
                    ServiceReminder.tenant_id == tenant_id, ServiceReminder.status.in_((ReminderStatus.SCHEDULED, ReminderStatus.DUE)),
                )
            )
        ).scalar_one()

        review_requests_sent = (
            await session.execute(
                select(func.count(ReviewRequest.id)).where(
                    ReviewRequest.tenant_id == tenant_id, ReviewRequest.status.in_(("REQUESTED", "RESPONDED", "RECEIVED")),
                )
            )
        ).scalar_one()

        positive_feedback = (
            await session.execute(
                select(func.count(CustomerFeedback.id)).where(CustomerFeedback.tenant_id == tenant_id, CustomerFeedback.sentiment == "POSITIVE")
            )
        ).scalar_one()
        negative_feedback = (
            await session.execute(
                select(func.count(CustomerFeedback.id)).where(CustomerFeedback.tenant_id == tenant_id, CustomerFeedback.sentiment == "NEGATIVE")
            )
        ).scalar_one()

        referrals = (await session.execute(select(Referral).where(Referral.tenant_id == tenant_id))).scalars().all()
        referral_leads = sum(1 for r in referrals if r.status != ReferralStatus.CREATED and r.status != ReferralStatus.CLICKED)
        referral_conversions = sum(1 for r in referrals if r.status in (ReferralStatus.CONVERTED, ReferralStatus.REWARDED))
        referral_revenue = sum((r.revenue_amount for r in referrals if r.revenue_amount is not None), Decimal("0"))

        repeat_customer_ids = {p.customer_id for p in profiles if p.lifecycle_state in (LifecycleState.REPEAT_CUSTOMER, LifecycleState.ADVOCATE)}
        repeat_revenue = Decimal("0")
        if repeat_customer_ids:
            invoices = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.customer_id.in_(repeat_customer_ids))
                )
            ).scalars().all()
            repeat_revenue = sum((inv.amount_paid for inv in invoices), Decimal("0"))

        open_exceptions = (
            await session.execute(
                select(func.count(OperationsException.id)).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.status == ExceptionStatus.OPEN,
                    OperationsException.type.in_(RETENTION_EXCEPTION_TYPES),
                )
            )
        ).scalar_one()

    return {
        "active_customers": active,
        "repeat_customers": repeat,
        "at_risk_customers": at_risk,
        "inactive_customers": inactive,
        "retention_opportunities_open": open_opportunities,
        "upcoming_service_reminders": upcoming_reminders,
        "review_requests_sent": review_requests_sent,
        "positive_feedback_count": positive_feedback,
        "negative_feedback_count": negative_feedback,
        "referral_leads": referral_leads,
        "referral_conversions": referral_conversions,
        "referral_revenue": str(referral_revenue),
        "repeat_customer_revenue": str(repeat_revenue),
        "open_retention_exception_count": open_exceptions,
        "needs_attention": open_exceptions > 0,
    }


@router.get("/analytics")
async def retention_analytics(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Every metric here documents its own definition and, when the
    underlying data can't support it reliably, says INSUFFICIENT DATA
    instead of a fabricated number."""
    tenant_id = current_user.tenant_id
    async with async_session_maker() as session:
        profiles = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id))
        ).scalars().all()
        total_customers = (await session.execute(select(func.count(Customer.id)).where(Customer.tenant_id == tenant_id))).scalar_one()

        referrals = (await session.execute(select(Referral).where(Referral.tenant_id == tenant_id))).scalars().all()

    result: dict[str, Any] = {}

    # Retention rate: repeat+advocate customers / customers with >=1 completed job.
    served = [p for p in profiles if p.jobs_completed_count >= 1]
    if not served:
        result["retention_rate"] = None
        result["retention_rate_note"] = "INSUFFICIENT DATA: no customers with a completed job yet."
    else:
        repeat = sum(1 for p in served if p.jobs_completed_count >= 2)
        result["retention_rate"] = round(repeat / len(served) * 100, 1)
        result["retention_rate_note"] = "Definition: customers with 2+ completed jobs / customers with 1+ completed job."

    # Repeat customer rate: same definition, over ALL customers (not just served).
    if total_customers == 0:
        result["repeat_customer_rate"] = None
        result["repeat_customer_rate_note"] = "INSUFFICIENT DATA: no customers yet."
    else:
        repeat_all = sum(1 for p in profiles if p.jobs_completed_count >= 2)
        result["repeat_customer_rate"] = round(repeat_all / total_customers * 100, 1)
        result["repeat_customer_rate_note"] = "Definition: customers with 2+ completed jobs / all customers."

    # Reactivation rate: customers ever marked REACTIVATED / customers ever AT_RISK or INACTIVE.
    ever_at_risk = [p for p in profiles if p.lifecycle_state in (LifecycleState.AT_RISK, LifecycleState.INACTIVE, LifecycleState.REACTIVATED)]
    if not ever_at_risk:
        result["customer_reactivation_rate"] = None
        result["customer_reactivation_rate_note"] = "INSUFFICIENT DATA: no customer has gone AT_RISK/INACTIVE yet."
    else:
        reactivated = sum(1 for p in ever_at_risk if p.lifecycle_state == LifecycleState.REACTIVATED)
        result["customer_reactivation_rate"] = round(reactivated / len(ever_at_risk) * 100, 1)
        result["customer_reactivation_rate_note"] = "Definition: customers currently REACTIVATED / customers currently AT_RISK, INACTIVE, or REACTIVATED."

    # Average customer value: total collected across all invoices / customers with >=1 invoice.
    async with async_session_maker() as session:
        invoices = (await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id))).scalars().all()
    customers_with_invoices = {inv.customer_id for inv in invoices}
    if not customers_with_invoices:
        result["average_customer_value"] = None
        result["average_customer_value_note"] = "INSUFFICIENT DATA: no invoices yet."
    else:
        total_collected = sum((inv.amount_paid for inv in invoices), Decimal("0"))
        result["average_customer_value"] = str((total_collected / len(customers_with_invoices)).quantize(Decimal("0.01")))
        result["average_customer_value_note"] = "Definition: total collected / customers with at least one invoice."

    # Referral conversion rate: referrals CONVERTED+ / referrals with a real Lead created.
    with_lead = [r for r in referrals if r.lead_id is not None]
    if not with_lead:
        result["referral_conversion_rate"] = None
        result["referral_conversion_rate_note"] = "INSUFFICIENT DATA: no referral has created a lead yet."
    else:
        converted = sum(1 for r in with_lead if r.status in (ReferralStatus.CONVERTED, ReferralStatus.REWARDED))
        result["referral_conversion_rate"] = round(converted / len(with_lead) * 100, 1)
        result["referral_conversion_rate_note"] = "Definition: referrals CONVERTED or REWARDED / referrals that created a real lead."

    referral_revenue = sum((r.revenue_amount for r in referrals if r.revenue_amount is not None), Decimal("0"))
    result["referral_revenue"] = str(referral_revenue)

    repeat_customer_ids = {p.customer_id for p in profiles if p.jobs_completed_count >= 2}
    async with async_session_maker() as session:
        repeat_invoices = (
            await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.customer_id.in_(repeat_customer_ids)))
        ).scalars().all() if repeat_customer_ids else []
    result["revenue_from_repeat_customers"] = str(sum((inv.amount_paid for inv in repeat_invoices), Decimal("0")))
    result["revenue_from_referrals"] = str(sum((r.collected_amount for r in referrals if r.collected_amount is not None), Decimal("0")))

    return result


@router.get("/customers/{customer_id}/health")
async def customer_health(
    customer_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, Any]:
    retention_service = RetentionService(async_session_maker, bus, ExceptionService(async_session_maker, bus))
    h = await retention_service.customer_service_history(current_user.tenant_id, customer_id)
    return {
        "lifecycle_state": h.lifecycle_state,
        "total_jobs": h.total_jobs,
        "completed_jobs": h.completed_jobs,
        "cancelled_jobs": h.cancelled_jobs,
        "total_invoiced": str(h.total_invoiced),
        "total_collected": str(h.total_collected),
        "open_balance": str(h.open_balance),
        "last_completed_job_at": h.last_completed_job_at.isoformat() if h.last_completed_job_at else None,
        "last_service_type": h.last_service_type,
        "average_days_between_jobs": h.average_days_between_jobs,
        "last_review_request_at": h.last_review_request_at.isoformat() if h.last_review_request_at else None,
        "last_referral_at": h.last_referral_at.isoformat() if h.last_referral_at else None,
    }
