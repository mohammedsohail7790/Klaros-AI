"""section: Customer Lifecycle, Service History, Retention Opportunities,
Service Reminders, Risk Signals, Advocate Candidates.

Every transition/detection here is driven by real rows (`Job`, `Invoice`,
`Payment`, `CommunicationLog`, `CustomerFeedback`, `OperationsException`)
or a real event — never an invented signal. `customer_service_history` is
computed on request, not persisted, the same "derived, never stale"
decision Phase 5 made for AR and Phase 6 made for campaign performance.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.finance import Invoice, InvoiceStatus
from app.models.operations import ExceptionType, Job, JobStatus
from app.models.retention import (
    AdvocateCandidate,
    AdvocateCandidateStatus,
    CustomerFeedback,
    CustomerLifecycleProfile,
    CustomerRiskSignal,
    LifecycleState,
    OpportunityStatus,
    OpportunityType,
    ReferralStatus,
    RetentionOpportunity,
    ReviewRequest,
    RiskSeverity,
    RiskSignalType,
    ServiceReminder,
    ReminderStatus,
)
from app.services.exception_service import ExceptionService

# Deterministic thresholds — static in-process constants, the same
# simplification Phase 3 (lead scoring), Phase 4 (delay tolerance), and
# Phase 6 (CAC/overspend thresholds) already accept; per-tenant
# configurability is a named limitation, not implemented.
SERVICE_INTERVAL_DAYS = 180
AT_RISK_GRACE_DAYS = 30
INACTIVE_DAYS = 365
POST_JOB_FOLLOWUP_DAYS = 3
PAYMENT_ISSUE_TOLERANCE_DAYS = 14
REMINDER_LEAD_DAYS = 14


class CustomerNotFoundError(Exception):
    pass


@dataclass
class ServiceHistory:
    total_jobs: int
    completed_jobs: int
    cancelled_jobs: int
    total_invoiced: Decimal
    total_collected: Decimal
    open_balance: Decimal
    last_completed_job_at: datetime | None
    last_service_type: str | None
    average_days_between_jobs: float | None
    lifecycle_state: str
    last_communication_at: datetime | None
    last_review_request_at: datetime | None
    last_referral_at: datetime | None


class RetentionService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus, exception_service: ExceptionService) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._exception_service = exception_service

    # --- Lifecycle -------------------------------------------------------------

    async def _get_or_create_profile(self, session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> CustomerLifecycleProfile:
        profile = (
            await session.execute(
                select(CustomerLifecycleProfile).where(
                    CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == customer_id
                )
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = CustomerLifecycleProfile(
                tenant_id=tenant_id, customer_id=customer_id, lifecycle_state=LifecycleState.NEW,
                jobs_completed_count=0, state_changed_at=datetime.now(timezone.utc),
            )
            session.add(profile)
            await session.flush()
        return profile

    async def handle_job_closed(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """Advances lifecycle off a real `job.closed` event. Idempotent:
        re-processing the same job (duplicate delivery) does not double-count
        `jobs_completed_count`, since the job's own `completed_at` is the
        source of truth this reads, not an incrementing counter driven by
        the event itself."""
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id or job.status != JobStatus.CLOSED:
                return

            closed_jobs = (
                await session.execute(
                    select(Job).where(
                        Job.tenant_id == tenant_id, Job.customer_id == job.customer_id, Job.status == JobStatus.CLOSED,
                    )
                )
            ).scalars().all()
            jobs_completed_count = len(closed_jobs)
            last_service_at = max((j.completed_at for j in closed_jobs if j.completed_at), default=now)
            first_service_at = min((j.completed_at for j in closed_jobs if j.completed_at), default=now)

            profile = await self._get_or_create_profile(session, tenant_id, job.customer_id)
            old_state = profile.lifecycle_state
            new_state = old_state
            reason = None

            if jobs_completed_count == 1:
                new_state = LifecycleState.FIRST_SERVICE
                reason = "First closed job."
            elif jobs_completed_count >= 2:
                if old_state in (LifecycleState.AT_RISK, LifecycleState.INACTIVE):
                    new_state = LifecycleState.REACTIVATED
                    reason = f"Completed a new job after being {old_state}."
                elif old_state != LifecycleState.ADVOCATE:
                    new_state = LifecycleState.REPEAT_CUSTOMER
                    reason = f"Completed job #{jobs_completed_count}."

            profile.jobs_completed_count = jobs_completed_count
            profile.first_service_at = first_service_at
            profile.last_service_at = last_service_at
            if new_state != old_state:
                profile.lifecycle_state = new_state
                profile.state_changed_at = now
                profile.state_reason = reason

            await session.commit()

        if new_state != old_state:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.RETENTION_LIFECYCLE_CHANGED, source="retention",
                entity_type="customer", entity_id=job.customer_id,
                payload={"customer_id": str(job.customer_id), "from": old_state, "to": new_state, "reason": reason},
            )

        await self._create_post_job_followup_opportunity(tenant_id, job)
        await self._create_review_eligibility(tenant_id, job)
        await self._schedule_service_reminder(tenant_id, job)

    async def detect_at_risk_and_inactive(self, tenant_id: uuid.UUID, *, as_of: date | None = None) -> list[uuid.UUID]:
        """Deterministic, on-demand — no scheduler required, mirrors Phase
        4/5/6's detect_overdue/detect_performance_exceptions pattern."""
        as_of = as_of or date.today()
        now = datetime.now(timezone.utc)
        changed: list[uuid.UUID] = []

        async with self._session_factory() as session:
            profiles = (
                await session.execute(
                    select(CustomerLifecycleProfile).where(
                        CustomerLifecycleProfile.tenant_id == tenant_id,
                        CustomerLifecycleProfile.lifecycle_state.in_(
                            (LifecycleState.FIRST_SERVICE, LifecycleState.ACTIVE, LifecycleState.REPEAT_CUSTOMER, LifecycleState.ADVOCATE)
                        ),
                    )
                )
            ).scalars().all()

            for profile in profiles:
                if profile.last_service_at is None:
                    continue
                days_since = (as_of - profile.last_service_at.date()).days
                old_state = profile.lifecycle_state
                if days_since >= INACTIVE_DAYS:
                    profile.lifecycle_state = LifecycleState.INACTIVE
                    profile.state_changed_at = now
                    profile.state_reason = f"No completed job in {days_since} days."
                    changed.append(profile.customer_id)
                elif days_since >= SERVICE_INTERVAL_DAYS + AT_RISK_GRACE_DAYS and old_state != LifecycleState.ADVOCATE:
                    profile.lifecycle_state = LifecycleState.AT_RISK
                    profile.state_changed_at = now
                    profile.state_reason = f"No completed job in {days_since} days (expected interval ~{SERVICE_INTERVAL_DAYS}d)."
                    changed.append(profile.customer_id)

            await session.commit()

        for customer_id in changed:
            await self._exception_service.create_exception(
                tenant_id, type=ExceptionType.CUSTOMER_AT_RISK, severity="MEDIUM", entity_type="customer",
                entity_id=customer_id, description="Customer is overdue for expected service or has gone inactive.",
                recommended_action="Enroll in a win-back retention campaign.",
            )
        return changed

    # --- Service history (derived) -------------------------------------------------------------

    async def customer_service_history(self, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> ServiceHistory:
        async with self._session_factory() as session:
            jobs = (
                await session.execute(select(Job).where(Job.tenant_id == tenant_id, Job.customer_id == customer_id))
            ).scalars().all()
            invoices = (
                await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.customer_id == customer_id))
            ).scalars().all()
            profile = (
                await session.execute(
                    select(CustomerLifecycleProfile).where(
                        CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == customer_id
                    )
                )
            ).scalar_one_or_none()
            last_review = (
                await session.execute(
                    select(ReviewRequest)
                    .where(ReviewRequest.tenant_id == tenant_id, ReviewRequest.customer_id == customer_id)
                    .order_by(ReviewRequest.created_at.desc())
                )
            ).scalars().first()
            from app.models.retention import Referral

            last_referral = (
                await session.execute(
                    select(Referral)
                    .where(Referral.tenant_id == tenant_id, Referral.referrer_customer_id == customer_id)
                    .order_by(Referral.created_at_referral.desc())
                )
            ).scalars().first()

        completed = sorted((j for j in jobs if j.status == JobStatus.CLOSED and j.completed_at), key=lambda j: j.completed_at)
        cancelled = [j for j in jobs if j.status == JobStatus.CANCELLED]

        avg_days = None
        if len(completed) >= 2:
            gaps = [(completed[i].completed_at - completed[i - 1].completed_at).days for i in range(1, len(completed))]
            avg_days = sum(gaps) / len(gaps)

        total_invoiced = sum((inv.total for inv in invoices), Decimal("0"))
        total_collected = sum((inv.amount_paid for inv in invoices), Decimal("0"))
        open_balance = sum((inv.amount_due for inv in invoices if inv.status not in (InvoiceStatus.VOID, InvoiceStatus.CANCELLED)), Decimal("0"))

        return ServiceHistory(
            total_jobs=len(jobs), completed_jobs=len(completed), cancelled_jobs=len(cancelled),
            total_invoiced=total_invoiced, total_collected=total_collected, open_balance=open_balance,
            last_completed_job_at=completed[-1].completed_at if completed else None,
            last_service_type=completed[-1].service_type if completed else None,
            average_days_between_jobs=avg_days,
            lifecycle_state=profile.lifecycle_state if profile else LifecycleState.NEW,
            last_communication_at=None,  # CommunicationLog has no customer_id FK — see PROJECT_STATUS.md
            last_review_request_at=last_review.requested_at if last_review else None,
            last_referral_at=last_referral.created_at_referral if last_referral else None,
        )

    # --- Opportunities -------------------------------------------------------------

    async def _create_opportunity(
        self, tenant_id: uuid.UUID, *, customer_id: uuid.UUID, type: str, reason: str, priority: str = "MEDIUM",
        source_event: str | None = None, recommended_action: str | None = None,
    ) -> tuple[RetentionOpportunity, bool]:
        """Phase 30 fix: backed by the real
        `uq_retention_opportunities_tenant_customer_type_open` unique
        constraint (tenant_id, customer_id, type, status), but the
        check-then-insert below never caught the `IntegrityError` a
        genuine concurrent duplicate trigger would raise — same class of
        defect as `LeadService.create_lead`/`ContractService.
        create_from_quote` (this same Phase 30 audit) and Phase 29's
        `CollectionService.schedule_next_action`. Realistic here because
        this method backs two real event-triggered callers (`job.closed`
        post-job-followup and positive-review referral eligibility), both
        plausible redelivery targets. Fixed with the same try/except/
        rollback/re-fetch CAS pattern."""
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(RetentionOpportunity).where(
                        RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.customer_id == customer_id,
                        RetentionOpportunity.type == type, RetentionOpportunity.status == OpportunityStatus.OPEN,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True

            opp = RetentionOpportunity(
                tenant_id=tenant_id, customer_id=customer_id, type=type, reason=reason, detected_at=now,
                priority=priority, status=OpportunityStatus.OPEN, source_event=source_event,
                recommended_action=recommended_action,
            )
            session.add(opp)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(RetentionOpportunity).where(
                            RetentionOpportunity.tenant_id == tenant_id,
                            RetentionOpportunity.customer_id == customer_id,
                            RetentionOpportunity.type == type,
                            RetentionOpportunity.status == OpportunityStatus.OPEN,
                        )
                    )
                ).scalar_one()
                return existing, True
            await session.refresh(opp)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_OPPORTUNITY_CREATED, source="retention",
            entity_type="retention_opportunity", entity_id=opp.id,
            # Phase 24: `reason` added so a consuming automation (e.g. the
            # referral-opportunity owner notification) has real, useful
            # content to show without needing to query anything itself —
            # fixing the payload at the producer rather than having the
            # automation handler reach into arbitrary data.
            payload={"customer_id": str(customer_id), "type": type, "reason": reason},
        )
        return opp, False

    async def _create_post_job_followup_opportunity(self, tenant_id: uuid.UUID, job: Job) -> None:
        await self._create_opportunity(
            tenant_id, customer_id=job.customer_id, type=OpportunityType.POST_JOB_FOLLOWUP,
            reason=f"Job {job.job_number} ({job.title}) closed — a real follow-up has never been sent for it.",
            priority="MEDIUM", source_event="job.closed",
            recommended_action="Send a post-job follow-up message.",
        )

    async def _create_review_eligibility(self, tenant_id: uuid.UUID, job: Job) -> None:
        """Review eligibility per the deterministic policy: job CLOSED,
        invoice PAID (or no invoice needed), no unresolved complaint for
        this customer, and no existing request for this job."""
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(ReviewRequest).where(ReviewRequest.tenant_id == tenant_id, ReviewRequest.job_id == job.id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return

            unresolved_negative = (
                await session.execute(
                    select(CustomerFeedback).where(
                        CustomerFeedback.tenant_id == tenant_id, CustomerFeedback.customer_id == job.customer_id,
                        CustomerFeedback.sentiment == "NEGATIVE",
                    )
                )
            ).scalars().first()
            if unresolved_negative is not None:
                return

            review = ReviewRequest(tenant_id=tenant_id, customer_id=job.customer_id, job_id=job.id, status="ELIGIBLE")
            session.add(review)
            await session.commit()

        await self._create_opportunity(
            tenant_id, customer_id=job.customer_id, type=OpportunityType.REVIEW_ELIGIBLE,
            reason=f"Job {job.job_number} closed with no unresolved complaint — eligible for a review request.",
            priority="LOW", source_event="job.closed", recommended_action="Send a review request.",
        )

    async def _schedule_service_reminder(self, tenant_id: uuid.UUID, job: Job) -> None:
        if job.completed_at is None:
            return
        reminder_date = (job.completed_at + timedelta(days=SERVICE_INTERVAL_DAYS)).date()
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(ServiceReminder).where(
                        ServiceReminder.tenant_id == tenant_id, ServiceReminder.source_job_id == job.id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            reminder = ServiceReminder(
                tenant_id=tenant_id, customer_id=job.customer_id, source_job_id=job.id, service_type=job.service_type,
                reminder_date=reminder_date, reason=f"~{SERVICE_INTERVAL_DAYS} days since {job.job_number}",
                status=ReminderStatus.SCHEDULED,
            )
            session.add(reminder)
            await session.commit()
            await session.refresh(reminder)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REMINDER_CREATED, source="retention",
            entity_type="service_reminder", entity_id=reminder.id, payload={"customer_id": str(job.customer_id)},
        )

    async def create_referral_eligibility_opportunity(self, tenant_id: uuid.UUID, customer_id: uuid.UUID, reason: str) -> None:
        await self._create_opportunity(
            tenant_id, customer_id=customer_id, type=OpportunityType.REFERRAL_ELIGIBLE, reason=reason,
            priority="LOW", source_event="retention.review_received", recommended_action="Send a referral invitation.",
        )

    async def mark_due_reminders(self, tenant_id: uuid.UUID, *, as_of: date | None = None) -> list[uuid.UUID]:
        as_of = as_of or date.today()
        async with self._session_factory() as session:
            due = (
                await session.execute(
                    select(ServiceReminder).where(
                        ServiceReminder.tenant_id == tenant_id, ServiceReminder.status == ReminderStatus.SCHEDULED,
                        ServiceReminder.reminder_date <= as_of,
                    )
                )
            ).scalars().all()
            ids = []
            for r in due:
                r.status = ReminderStatus.DUE
                ids.append(r.id)
            await session.commit()

        for reminder_id in ids:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.RETENTION_REMINDER_DUE, source="retention",
                entity_type="service_reminder", entity_id=reminder_id, payload={},
            )
        return ids

    async def update_reminder_status(self, tenant_id: uuid.UUID, reminder_id: uuid.UUID, status: str) -> ServiceReminder:
        async with self._session_factory() as session:
            reminder = await session.get(ServiceReminder, reminder_id)
            if reminder is None or reminder.tenant_id != tenant_id:
                raise ValueError("Service reminder not found")
            reminder.status = status
            await session.commit()
            await session.refresh(reminder)
        return reminder

    async def update_opportunity_status(self, tenant_id: uuid.UUID, opportunity_id: uuid.UUID, status: str) -> RetentionOpportunity:
        async with self._session_factory() as session:
            opp = await session.get(RetentionOpportunity, opportunity_id)
            if opp is None or opp.tenant_id != tenant_id:
                raise ValueError("Retention opportunity not found")
            opp.status = status
            await session.commit()
            await session.refresh(opp)
        return opp

    # --- Risk signals -------------------------------------------------------------

    async def _create_risk_signal(
        self, tenant_id: uuid.UUID, *, customer_id: uuid.UUID, signal_type: str, severity: str, description: str,
    ) -> tuple[CustomerRiskSignal, bool]:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(CustomerRiskSignal).where(
                        CustomerRiskSignal.tenant_id == tenant_id, CustomerRiskSignal.customer_id == customer_id,
                        CustomerRiskSignal.signal_type == signal_type, CustomerRiskSignal.resolved == False,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True
            signal = CustomerRiskSignal(
                tenant_id=tenant_id, customer_id=customer_id, signal_type=signal_type, severity=severity,
                description=description, detected_at=datetime.now(timezone.utc), resolved=False,
            )
            session.add(signal)
            await session.commit()
            await session.refresh(signal)
        return signal, False

    async def detect_payment_issue_risk(self, tenant_id: uuid.UUID, *, as_of: date | None = None) -> list[uuid.UUID]:
        as_of = as_of or date.today()
        async with self._session_factory() as session:
            overdue = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.OVERDUE,
                    )
                )
            ).scalars().all()

        flagged: list[uuid.UUID] = []
        for inv in overdue:
            days_overdue = (as_of - inv.due_date).days
            if days_overdue >= PAYMENT_ISSUE_TOLERANCE_DAYS:
                _, deduped = await self._create_risk_signal(
                    tenant_id, customer_id=inv.customer_id, signal_type=RiskSignalType.PAYMENT_ISSUE,
                    severity=RiskSeverity.HIGH if days_overdue > 60 else RiskSeverity.MEDIUM,
                    description=f"Invoice {inv.invoice_number} is {days_overdue} days overdue (${inv.amount_due} due).",
                )
                if not deduped:
                    flagged.append(inv.customer_id)
        return flagged

    # --- Advocate candidates -------------------------------------------------------------

    async def identify_advocate_candidates(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        """Deterministic: >=2 completed jobs, no unresolved NEGATIVE
        feedback, no open unresolved risk signal, all invoices for the
        customer are PAID/VOID (nothing overdue)."""
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            profiles = (
                await session.execute(
                    select(CustomerLifecycleProfile).where(
                        CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.jobs_completed_count >= 2,
                    )
                )
            ).scalars().all()

            created: list[uuid.UUID] = []
            for profile in profiles:
                negative = (
                    await session.execute(
                        select(CustomerFeedback).where(
                            CustomerFeedback.tenant_id == tenant_id, CustomerFeedback.customer_id == profile.customer_id,
                            CustomerFeedback.sentiment == "NEGATIVE",
                        )
                    )
                ).scalars().first()
                if negative is not None:
                    continue

                open_risk = (
                    await session.execute(
                        select(CustomerRiskSignal).where(
                            CustomerRiskSignal.tenant_id == tenant_id, CustomerRiskSignal.customer_id == profile.customer_id,
                            CustomerRiskSignal.resolved == False,  # noqa: E712
                        )
                    )
                ).scalars().first()
                if open_risk is not None:
                    continue

                overdue_invoices = (
                    await session.execute(
                        select(Invoice).where(
                            Invoice.tenant_id == tenant_id, Invoice.customer_id == profile.customer_id,
                            Invoice.status == InvoiceStatus.OVERDUE,
                        )
                    )
                ).scalars().first()
                if overdue_invoices is not None:
                    continue

                existing = (
                    await session.execute(
                        select(AdvocateCandidate).where(
                            AdvocateCandidate.tenant_id == tenant_id, AdvocateCandidate.customer_id == profile.customer_id,
                            AdvocateCandidate.status == AdvocateCandidateStatus.PENDING,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                positive_feedback = (
                    await session.execute(
                        select(CustomerFeedback).where(
                            CustomerFeedback.tenant_id == tenant_id, CustomerFeedback.customer_id == profile.customer_id,
                            CustomerFeedback.sentiment == "POSITIVE",
                        )
                    )
                ).scalars().first()

                signals = [f"{profile.jobs_completed_count} completed jobs", "no unresolved complaints", "no overdue invoices"]
                if positive_feedback is not None:
                    signals.append("positive feedback on file")

                candidate = AdvocateCandidate(
                    tenant_id=tenant_id, customer_id=profile.customer_id,
                    reason=f"Customer has completed {profile.jobs_completed_count} jobs with no unresolved complaints or overdue invoices.",
                    signals=", ".join(signals), priority="MEDIUM", status=AdvocateCandidateStatus.PENDING,
                    identified_at=now,
                )
                session.add(candidate)
                created.append(profile.customer_id)

                if profile.lifecycle_state == LifecycleState.REPEAT_CUSTOMER and len(signals) >= 4:
                    profile.lifecycle_state = LifecycleState.ADVOCATE
                    profile.state_changed_at = now
                    profile.state_reason = "Identified as advocate candidate: " + ", ".join(signals)

            await session.commit()
        return created
