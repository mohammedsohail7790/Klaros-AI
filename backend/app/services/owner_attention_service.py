"""Phase 26: the Owner Attention Queue — a deterministic, read-only
aggregation of "things the owner can act on right now", built entirely
from the existing domain tables (Lead, Quote, Contract, Job/QA exception,
Invoice, RetentionOpportunity, ApprovalRequest, CompanyMemory,
AutomationExecution). Not a new domain model, not a second CRM/finance/
automation system — every row here is read straight from the same tables
every other real endpoint in this codebase already reads from.

This complements, and deliberately does not replace, `InsightService`
(Morning Brief's own aggregation) — `InsightService`'s snapshots answer
"what happened in the last 24h" for a narrative brief; this module answers
"what, right now, deserves the owner's attention, ranked" for a live
queue. Both read the same underlying rows.

Priority is a deterministic, explainable score — never an LLM call. Each
item explains WHY it was prioritized (age, monetary value, overdue state,
failure state) so an owner (or a future maintainer) can verify the
ranking makes sense without trusting a black box.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.ai_invocation import AIInvocationLog
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.automation import AutomationExecution, ExecutionStatus
from app.models.company_memory import CompanyMemory, MemoryStatus, MemoryType
from app.models.contract import Contract, ContractStatus
from app.models.crm import Appointment, Lead, LeadStatus, QualificationStatus
from app.models.finance import Invoice, InvoiceStatus
from app.models.integration import ConnectionStatus, IntegrationConnection, WebhookEvent, WebhookProcessingStatus
from app.models.operations import ExceptionStatus, ExceptionType, Job, JobStatus, OperationsException
from app.models.quote import Quote, QuoteStatus
from app.models.retention import OpportunityStatus, OpportunityType, RetentionOpportunity


class AttentionCategory:
    QUALIFIED_LEAD_NO_APPOINTMENT = "qualified_lead_no_appointment"
    QUOTE_STALE = "quote_stale"
    CONTRACT_PENDING = "contract_pending"
    JOB_QA_FAILED = "job_qa_failed"
    JOB_BLOCKED = "job_blocked"
    INVOICE_OVERDUE = "invoice_overdue"
    RETENTION_OPPORTUNITY = "retention_opportunity"
    REFERRAL_OPPORTUNITY = "referral_opportunity"
    AI_APPROVAL_REQUIRED = "ai_approval_required"
    AI_FEEDBACK_PENDING = "ai_feedback_pending"
    AUTOMATION_FAILED = "automation_failed"
    PROVIDER_AUTH_FAILED = "provider_auth_failed"
    WEBHOOK_PROCESSING_FAILED = "webhook_processing_failed"
    AI_EXECUTION_FAILED = "ai_execution_failed"


class Priority:
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# entity_type -> frontend route builder. The ONLY place this mapping is
# defined — reused by both the API response (so the frontend never has to
# duplicate this switch) and, if a maintainer needs it, this docstring is
# the map to update. Every route here already exists (Phase 1-25); none
# were created for this phase.
_ENTITY_LINKS = {
    "lead": lambda entity_id: f"/leads/{entity_id}",
    "quote": lambda entity_id: f"/quotes/{entity_id}",
    "contract": lambda entity_id: f"/contracts/{entity_id}",
    "job": lambda entity_id: f"/jobs/{entity_id}",
    "invoice": lambda entity_id: f"/finance/invoices/{entity_id}",
    "retention_opportunity": lambda entity_id: "/retention/opportunities",
    "approval_request": lambda entity_id: f"/approvals?id={entity_id}",
    "company_memory": lambda entity_id: "/settings/memory",
    "automation_execution": lambda entity_id: "/automations",
    "integration_connection": lambda entity_id: "/settings/integrations",
    "webhook_event": lambda entity_id: "/settings/integrations",
    "ai_invocation": lambda entity_id: "/ai-activity",
}


def _link_for(entity_type: str, entity_id: uuid.UUID) -> str:
    builder = _ENTITY_LINKS.get(entity_type)
    return builder(entity_id) if builder else "/dashboard"


def _priority_for_score(score: int) -> str:
    if score >= 80:
        return Priority.CRITICAL
    if score >= 55:
        return Priority.HIGH
    if score >= 30:
        return Priority.MEDIUM
    return Priority.LOW


def _age_bonus(days: int, *, per_day: int = 2, cap: int = 30) -> int:
    return min(max(days, 0) * per_day, cap)


def _value_bonus(amount: Decimal | None) -> int:
    if amount is None:
        return 0
    if amount >= 5000:
        return 20
    if amount >= 1000:
        return 10
    if amount > 0:
        return 5
    return 0


@dataclass
class AttentionItem:
    category: str
    priority: str
    score: int
    title: str
    reason: str
    entity_type: str
    entity_id: str
    link: str
    age_days: int | None = None
    monetary_value: str | None = None

    def to_dict(self) -> dict:
        return {
            "category": self.category, "priority": self.priority, "score": self.score,
            "title": self.title, "reason": self.reason, "entity_type": self.entity_type,
            "entity_id": self.entity_id, "link": self.link, "age_days": self.age_days,
            "monetary_value": self.monetary_value,
        }


class OwnerAttentionService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_attention_queue(self, tenant_id: uuid.UUID, *, limit: int = 50) -> list[AttentionItem]:
        now = datetime.now(timezone.utc)
        items: list[AttentionItem] = []
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            # --- Qualified leads with no appointment booked -----------
            leads = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == tenant_id,
                        Lead.qualification_status == QualificationStatus.QUALIFIED,
                        Lead.status.not_in([LeadStatus.LOST, LeadStatus.CONVERTED]),
                    )
                )
            ).scalars().all()
            if leads:
                leads_with_appt = {
                    row for row in (
                        await session.execute(
                            select(Appointment.lead_id).where(
                                Appointment.tenant_id == tenant_id, Appointment.lead_id.is_not(None)
                            )
                        )
                    ).scalars().all()
                }
                for lead in leads:
                    if lead.id in leads_with_appt:
                        continue
                    updated = lead.updated_at if lead.updated_at.tzinfo else lead.updated_at.replace(tzinfo=timezone.utc)
                    age_days = max((now - updated).days, 0)
                    score = 40 + _age_bonus(age_days)
                    items.append(AttentionItem(
                        category=AttentionCategory.QUALIFIED_LEAD_NO_APPOINTMENT, priority=_priority_for_score(score),
                        score=score, title=f"Qualified lead {lead.name} has no appointment",
                        reason=f"Qualified {age_days} day(s) ago, no appointment booked yet.",
                        entity_type="lead", entity_id=str(lead.id), link=_link_for("lead", lead.id), age_days=age_days,
                    ))

            # --- Stale quotes (sent/viewed, no response for N days) ---
            settings = get_settings()
            stale_cutoff = now - timedelta(days=settings.STALE_QUOTE_FOLLOWUP_DAYS)
            stale_quotes = (
                await session.execute(
                    select(Quote).where(
                        Quote.tenant_id == tenant_id, Quote.status.in_((QuoteStatus.SENT, QuoteStatus.VIEWED)),
                        Quote.sent_at.is_not(None), Quote.sent_at <= stale_cutoff,
                    )
                )
            ).scalars().all()
            for q in stale_quotes:
                sent_at = q.sent_at if q.sent_at.tzinfo else q.sent_at.replace(tzinfo=timezone.utc)
                age_days = max((now - sent_at).days, 0)
                score = 35 + _age_bonus(age_days) + _value_bonus(q.total)
                items.append(AttentionItem(
                    category=AttentionCategory.QUOTE_STALE, priority=_priority_for_score(score), score=score,
                    title=f"Quote {q.quote_number} has had no response",
                    reason=f"Sent {age_days} day(s) ago, worth ${q.total}, no accept/decline yet.",
                    entity_type="quote", entity_id=str(q.id), link=_link_for("quote", q.id),
                    age_days=age_days, monetary_value=str(q.total),
                ))

            # --- Contracts pending signature ---------------------------
            pending_contracts = (
                await session.execute(
                    select(Contract).where(
                        Contract.tenant_id == tenant_id, Contract.status.in_((ContractStatus.SENT, ContractStatus.VIEWED)),
                    )
                )
            ).scalars().all()
            for c in pending_contracts:
                if c.sent_at is not None:
                    sent_at = c.sent_at if c.sent_at.tzinfo else c.sent_at.replace(tzinfo=timezone.utc)
                    age_days = max((now - sent_at).days, 0)
                else:
                    age_days = 0
                score = 45 + _age_bonus(age_days)
                items.append(AttentionItem(
                    category=AttentionCategory.CONTRACT_PENDING, priority=_priority_for_score(score), score=score,
                    title=f"Contract {c.contract_number} awaiting signature",
                    reason=f"Sent {age_days} day(s) ago, not yet signed.",
                    entity_type="contract", entity_id=str(c.id), link=_link_for("contract", c.id), age_days=age_days,
                ))

            # --- QA failures (open OperationsException, QA_FAILURE) ---
            qa_exceptions = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id, OperationsException.status == ExceptionStatus.OPEN,
                        OperationsException.type == ExceptionType.QA_FAILURE,
                    )
                )
            ).scalars().all()
            for exc in qa_exceptions:
                created = exc.created_at if exc.created_at.tzinfo else exc.created_at.replace(tzinfo=timezone.utc)
                age_days = max((now - created).days, 0)
                score = 75 + _age_bonus(age_days, per_day=3, cap=20)
                items.append(AttentionItem(
                    category=AttentionCategory.JOB_QA_FAILED, priority=_priority_for_score(score), score=score,
                    title="QA failed on a job", reason=exc.description,
                    entity_type=exc.entity_type or "job", entity_id=str(exc.entity_id),
                    link=_link_for(exc.entity_type or "job", exc.entity_id), age_days=age_days,
                ))

            # --- Blocked jobs -------------------------------------------
            blocked_jobs = (
                await session.execute(select(Job).where(Job.tenant_id == tenant_id, Job.status == JobStatus.BLOCKED))
            ).scalars().all()
            for job in blocked_jobs:
                score = 70
                items.append(AttentionItem(
                    category=AttentionCategory.JOB_BLOCKED, priority=_priority_for_score(score), score=score,
                    title=f"Job {job.job_number} is blocked", reason=f"Job {job.job_number} ({job.title}) is in BLOCKED status.",
                    entity_type="job", entity_id=str(job.id), link=_link_for("job", job.id),
                ))

            # --- Overdue invoices ----------------------------------------
            overdue_invoices = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.OVERDUE)
                )
            ).scalars().all()
            today = date.today()
            for inv in overdue_invoices:
                age_days = max((today - inv.due_date).days, 0)
                score = 50 + _age_bonus(age_days) + _value_bonus(inv.amount_due)
                items.append(AttentionItem(
                    category=AttentionCategory.INVOICE_OVERDUE, priority=_priority_for_score(score), score=score,
                    title=f"Invoice {inv.invoice_number} is overdue",
                    reason=f"Overdue by {age_days} day(s), ${inv.amount_due} outstanding.",
                    entity_type="invoice", entity_id=str(inv.id), link=_link_for("invoice", inv.id),
                    age_days=age_days, monetary_value=str(inv.amount_due),
                ))

            # --- Retention / referral opportunities -----------------------
            opportunities = (
                await session.execute(
                    select(RetentionOpportunity).where(
                        RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.status == OpportunityStatus.OPEN,
                    )
                )
            ).scalars().all()
            for opp in opportunities:
                detected = opp.detected_at if opp.detected_at.tzinfo else opp.detected_at.replace(tzinfo=timezone.utc)
                age_days = max((now - detected).days, 0)
                is_referral = opp.type == OpportunityType.REFERRAL_ELIGIBLE
                category = AttentionCategory.REFERRAL_OPPORTUNITY if is_referral else AttentionCategory.RETENTION_OPPORTUNITY
                base = 20 if is_referral else 40
                score = base + _age_bonus(age_days, per_day=1, cap=15)
                items.append(AttentionItem(
                    category=category, priority=_priority_for_score(score), score=score,
                    title=f"{'Referral' if is_referral else 'Retention'} opportunity: {opp.type}",
                    reason=opp.reason, entity_type="retention_opportunity", entity_id=str(opp.id),
                    link=_link_for("retention_opportunity", opp.id), age_days=age_days,
                ))

            # --- AI approvals required ------------------------------------
            pending_approvals = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for ar in pending_approvals:
                created = ar.created_at if ar.created_at.tzinfo else ar.created_at.replace(tzinfo=timezone.utc)
                age_days = max((now - created).days, 0)
                score = 60 + _age_bonus(age_days, per_day=3, cap=25)
                items.append(AttentionItem(
                    category=AttentionCategory.AI_APPROVAL_REQUIRED, priority=_priority_for_score(score), score=score,
                    title=f"Approval needed: {ar.action_type}", reason=ar.reason,
                    entity_type="approval_request", entity_id=str(ar.id), link=_link_for("approval_request", ar.id),
                    age_days=age_days,
                ))

            # --- AI feedback awaiting review -------------------------------
            pending_feedback = (
                await session.execute(
                    select(CompanyMemory).where(
                        CompanyMemory.tenant_id == tenant_id, CompanyMemory.status == MemoryStatus.PENDING,
                        CompanyMemory.memory_type == MemoryType.AI_FEEDBACK,
                    )
                )
            ).scalars().all()
            for mem in pending_feedback:
                created = mem.created_at if mem.created_at.tzinfo else mem.created_at.replace(tzinfo=timezone.utc)
                age_days = max((now - created).days, 0)
                score = 30 + _age_bonus(age_days, per_day=1, cap=15)
                items.append(AttentionItem(
                    category=AttentionCategory.AI_FEEDBACK_PENDING, priority=_priority_for_score(score), score=score,
                    title="AI feedback awaiting review", reason=mem.value,
                    entity_type="company_memory", entity_id=str(mem.id), link=_link_for("company_memory", mem.id),
                    age_days=age_days,
                ))

            # --- Recent automation failures (last 7 days) -------------------
            since = now - timedelta(days=7)
            failed_executions = (
                await session.execute(
                    select(AutomationExecution).where(
                        AutomationExecution.tenant_id == tenant_id, AutomationExecution.status == ExecutionStatus.FAILED,
                        AutomationExecution.started_at >= since,
                    )
                )
            ).scalars().all()
            for ex in failed_executions:
                started = ex.started_at if ex.started_at.tzinfo else ex.started_at.replace(tzinfo=timezone.utc)
                age_days = max((now - started).days, 0)
                score = 70 - _age_bonus(age_days, per_day=5, cap=30)  # a fresh failure matters more than a week-old one
                score = max(score, 20)
                items.append(AttentionItem(
                    category=AttentionCategory.AUTOMATION_FAILED, priority=_priority_for_score(score), score=score,
                    title="An automation execution failed", reason=ex.error or "Execution failed with no recorded error.",
                    entity_type="automation_execution", entity_id=str(ex.id),
                    link=_link_for("automation_execution", ex.id), age_days=age_days,
                ))

            # --- Production observability (this phase): provider auth
            # failures, webhook processing failures, and repeated AI
            # execution failures — all read straight from existing
            # tables (IntegrationConnection, WebhookEvent,
            # AIInvocationLog), same as every category above. Never a
            # new failure/incident model. Deliberately aggregated (one
            # item per provider/failure-type, not one per row) so a
            # burst of the same underlying problem never floods the
            # queue — the same "avoid alert storms" discipline the
            # push-notification side (NotificationService dedupe_key)
            # also applies. -----------------------------------------
            connections = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.tenant_id == tenant_id,
                        IntegrationConnection.status == ConnectionStatus.ERROR,
                    )
                )
            ).scalars().all()
            for conn in connections:
                verified = conn.last_verified_at
                if verified is not None and verified.tzinfo is None:
                    verified = verified.replace(tzinfo=timezone.utc)
                age_days = max((now - verified).days, 0) if verified else 0
                score = 65 - _age_bonus(age_days, per_day=3, cap=20)
                score = max(score, 25)
                items.append(AttentionItem(
                    category=AttentionCategory.PROVIDER_AUTH_FAILED, priority=_priority_for_score(score), score=score,
                    title=f"{conn.provider} connection needs attention",
                    reason=conn.last_error or "The last connection check failed with no recorded detail.",
                    entity_type="integration_connection", entity_id=str(conn.id),
                    link=_link_for("integration_connection", conn.id), age_days=age_days,
                ))

            # --- Recent webhook processing failures (last 7 days),
            # aggregated per provider — a real WebhookEvent row already
            # exists for every failure (Stripe/Twilio/marketplace),
            # never a second failure-tracking table. -----------------
            since_webhooks = now - timedelta(days=7)
            failed_webhooks = (
                await session.execute(
                    select(WebhookEvent).where(
                        WebhookEvent.tenant_id == tenant_id,
                        WebhookEvent.status == WebhookProcessingStatus.FAILED,
                        WebhookEvent.created_at >= since_webhooks,
                    )
                )
            ).scalars().all()
            by_provider: dict[str, list] = {}
            for wh in failed_webhooks:
                by_provider.setdefault(wh.provider, []).append(wh)
            for provider, rows in by_provider.items():
                latest = max(rows, key=lambda r: r.created_at)
                created = latest.created_at if latest.created_at.tzinfo else latest.created_at.replace(tzinfo=timezone.utc)
                age_days = max((now - created).days, 0)
                score = 60 + min(len(rows) * 5, 20) - _age_bonus(age_days, per_day=4, cap=25)
                score = max(score, 25)
                items.append(AttentionItem(
                    category=AttentionCategory.WEBHOOK_PROCESSING_FAILED, priority=_priority_for_score(score), score=score,
                    title=f"{len(rows)} {provider} webhook(s) failed to process",
                    reason=latest.error_detail or "Processing failed with no recorded detail.",
                    entity_type="webhook_event", entity_id=str(latest.id),
                    link=_link_for("webhook_event", latest.id), age_days=age_days,
                ))

            # --- Repeated AI execution failures (last 24h), grouped by
            # operation — a single transient failure is normal and
            # already retried at the provider-client level; only a
            # genuine PATTERN (>=3 in 24h for the same operation)
            # deserves owner attention, matching the "never alert for
            # every individual transient error" rule. -----------------
            since_ai = now - timedelta(hours=24)
            failed_ai = (
                await session.execute(
                    select(AIInvocationLog).where(
                        AIInvocationLog.tenant_id == tenant_id, AIInvocationLog.success.is_(False),
                        AIInvocationLog.created_at >= since_ai,
                    )
                )
            ).scalars().all()
            by_operation: dict[str, list] = {}
            for inv in failed_ai:
                by_operation.setdefault(inv.operation, []).append(inv)
            for operation, rows in by_operation.items():
                if len(rows) < 3:
                    continue
                latest = max(rows, key=lambda r: r.created_at)
                score = 50 + min(len(rows) * 5, 30)
                items.append(AttentionItem(
                    category=AttentionCategory.AI_EXECUTION_FAILED, priority=_priority_for_score(score), score=score,
                    title=f"AI '{operation}' has failed {len(rows)} times in the last 24h",
                    reason=f"error_type={latest.error_type or 'unknown'}, provider={latest.provider}",
                    entity_type="ai_invocation", entity_id=str(latest.id),
                    link=_link_for("ai_invocation", latest.id), age_days=0,
                ))

        items.sort(key=lambda i: i.score, reverse=True)
        return items[:limit]
