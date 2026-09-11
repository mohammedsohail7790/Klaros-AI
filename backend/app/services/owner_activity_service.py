"""Phase 27: the Owner Activity Feed — a read-only, human-readable
projection of business history, built entirely from existing persisted
sources. Not a new event bus, event store, or audit system: every row
returned here is read straight from a table another real endpoint in
this codebase already reads from (the exact same discipline
`OwnerAttentionService`, Phase 26, already established).

SOURCE OF TRUTH MATRIX (Step 1/4) — one activity type, one source table,
one timestamp column, never derived from more than one table's identity:

| Activity type            | Source table         | Timestamp column        |
|---------------------------|-----------------------|--------------------------|
| LEAD_CREATED               | Lead                  | created_at               |
| LEAD_QUALIFIED              | Lead                  | updated_at (qualification_status=QUALIFIED) |
| APPOINTMENT_CREATED          | Appointment            | created_at               |
| QUOTE_CREATED                | Quote                  | created_at               |
| QUOTE_EXPIRED                  | Quote                  | updated_at (status=EXPIRED) |
| CONTRACT_CREATED                | Contract                | created_at               |
| CONTRACT_SIGNED                    | Contract                | decided_at (status=SIGNED) |
| JOB_CREATED                          | Job                       | created_at               |
| QA_FAILED                              | OperationsException (type=QA_FAILURE) | created_at |
| QA_PASSED                                | JobQA                     | updated_at (status=PASSED) |
| SIGNOFF_COMPLETED                          | CustomerSignoff          | signed_at                |
| INVOICE_CREATED                              | Invoice                  | created_at               |
| INVOICE_OVERDUE                                | Invoice                  | updated_at (status=OVERDUE) |
| PAYMENT_RECEIVED                                 | Payment                  | received_at              |
| RETENTION_OPPORTUNITY                              | RetentionOpportunity (non-referral types) | detected_at |
| REFERRAL_OPPORTUNITY                                 | RetentionOpportunity (type=REFERRAL_ELIGIBLE) | detected_at |
| AUTOMATION_EXECUTED                                    | AutomationExecution (status=COMPLETED) | completed_at |
| AUTOMATION_FAILED                                        | AutomationExecution (status=FAILED) | completed_at |
| AI_DECISION_PROPOSED                                       | ApprovalRequest (requested_by_type=AI) | created_at |
| AI_ACTION_APPROVED                                            | ApprovalRequest (status=APPROVED, requested_by_type=AI) | decided_at |
| AI_ACTION_EXECUTED                                               | AuditLog (actor_type=AI, result=success) | created_at |
| AI_FEEDBACK_PENDING                                                | CompanyMemory (memory_type=AI_FEEDBACK, status=PENDING) | created_at |
| AI_FEEDBACK_CONFIRMED                                                | CompanyMemory (memory_type=AI_FEEDBACK, status=ACTIVE) | updated_at |

Deliberately NOT implemented: JOB_STATUS_CHANGED (the mission's own
example list includes it, but `Job.updated_at` alone cannot say WHICH
status changed or reliably avoid noise from unrelated field edits — no
single reliable timestamp+meaning source exists for it without adding a
job-status-history table, which Step 29 forbids introducing for this
phase). JOB_CREATED and the QA/signoff/invoice milestones already cover
the operationally meaningful moments in a job's life.

Every source query below filters on the caller-supplied `tenant_id`
(always `current_user.tenant_id` from the API layer — never a
client-supplied value) and is bounded by `_PER_SOURCE_LIMIT`. There is no
N+1 pattern: a fixed, small number of independently-bounded queries run
once per request, merged and sorted in Python, then paginated — the same
shape `OwnerAttentionService` already uses successfully.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.automation import AutomationExecution, ExecutionStatus
from app.models.company_memory import CompanyMemory, MemoryStatus, MemoryType
from app.models.contract import Contract, ContractStatus
from app.models.crm import Appointment, Lead, QualificationStatus
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.models.operations import (
    CustomerSignoff,
    ExceptionType,
    Job,
    JobQA,
    OperationsException,
    QAStatus,
)
from app.models.quote import Quote, QuoteStatus
from app.models.retention import OpportunityType, RetentionOpportunity

# Bound per-source query so the total work is always O(number of
# categories), never proportional to full table size — mirrors
# OwnerAttentionService's own bounding discipline.
_PER_SOURCE_LIMIT = 100

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

CATEGORY_CRM = "CRM"
CATEGORY_SALES = "SALES"
CATEGORY_CONTRACT = "CONTRACT"
CATEGORY_OPERATIONS = "OPERATIONS"
CATEGORY_QA = "QA"
CATEGORY_FINANCE = "FINANCE"
CATEGORY_RETENTION = "RETENTION"
CATEGORY_REFERRAL = "REFERRAL"
CATEGORY_AUTOMATION = "AUTOMATION"
CATEGORY_AI = "AI"

_ENTITY_LINKS = {
    "lead": lambda entity_id: f"/leads/{entity_id}",
    "quote": lambda entity_id: f"/quotes/{entity_id}",
    "contract": lambda entity_id: f"/contracts/{entity_id}",
    "job": lambda entity_id: f"/jobs/{entity_id}",
    "invoice": lambda entity_id: f"/finance/invoices/{entity_id}",
    "customer": lambda entity_id: f"/customers/{entity_id}",
    "retention_opportunity": lambda entity_id: "/retention/opportunities",
    "approval_request": lambda entity_id: f"/approvals?id={entity_id}",
    "company_memory": lambda entity_id: "/settings/memory",
    "automation_execution": lambda entity_id: "/automations",
}


def _link_for(entity_type: str | None, entity_id: uuid.UUID | None) -> str | None:
    if entity_type is None or entity_id is None:
        return None
    builder = _ENTITY_LINKS.get(entity_type)
    return builder(entity_id) if builder else None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Activity:
    id: str  # stable synthetic id: f"{source_table}:{row_id}" — never a new primary key
    timestamp: datetime
    activity_type: str
    category: str
    title: str
    description: str
    severity: str  # INFO | WARNING | ERROR
    actor_type: str
    actor_name: str | None
    entity_type: str | None
    entity_id: str | None
    entity_label: str | None
    link: str | None
    status: str | None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "timestamp": self.timestamp.isoformat(), "activity_type": self.activity_type,
            "category": self.category, "title": self.title, "description": self.description,
            "severity": self.severity, "actor_type": self.actor_type, "actor_name": self.actor_name,
            "entity_type": self.entity_type, "entity_id": self.entity_id, "entity_label": self.entity_label,
            "link": self.link, "status": self.status, "metadata": self.metadata,
        }


def _actor_name(actor_type: str) -> str:
    """Step 10: normalize actor identity safely — never expose internal
    provider/implementation detail, only a stable human-facing label."""
    mapping = {
        ActorType.AI: "Klaros AI", "ai": "Klaros AI",
        ActorType.SYSTEM: "System", "system": "System",
        ActorType.USER: "Owner/Team", "human": "Owner/Team", ActorType.WORKFLOW: "Automation",
    }
    return mapping.get(actor_type, "System")


class OwnerActivityService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_activity(
        self, tenant_id: uuid.UUID, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, category: str | None = None,
    ) -> tuple[list[Activity], int]:
        """Returns (page_of_activities, total_count_before_pagination).
        `category`, when given, filters the merged result — deterministic,
        applied after merge so ordering/pagination stay simple and
        correct regardless of which category(ies) are requested."""
        page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        page = max(page, 1)

        all_activities = await self._collect_all(tenant_id)
        if category:
            all_activities = [a for a in all_activities if a.category == category]

        # Deterministic order: timestamp desc, then id asc as a stable
        # tiebreaker for identical timestamps (Step 7).
        all_activities.sort(key=lambda a: (a.timestamp, a.id), reverse=True)

        total = len(all_activities)
        start = (page - 1) * page_size
        return all_activities[start : start + page_size], total

    async def _collect_all(self, tenant_id: uuid.UUID) -> list[Activity]:
        activities: list[Activity] = []
        async with self._session_factory() as session:
            # --- LEAD_CREATED / LEAD_QUALIFIED ---------------------------
            leads = (
                await session.execute(
                    select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for lead in leads:
                activities.append(Activity(
                    id=f"lead_created:{lead.id}", timestamp=_aware(lead.created_at), activity_type="LEAD_CREATED",
                    category=CATEGORY_CRM, title=f"New lead: {lead.name}", description=f"Source: {lead.source}",
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="lead", entity_id=str(lead.id), entity_label=lead.name,
                    link=_link_for("lead", lead.id), status=lead.status,
                ))
                if lead.qualification_status == QualificationStatus.QUALIFIED:
                    activities.append(Activity(
                        id=f"lead_qualified:{lead.id}", timestamp=_aware(lead.updated_at), activity_type="LEAD_QUALIFIED",
                        category=CATEGORY_CRM, title=f"Lead qualified: {lead.name}", description=f"Score: {lead.lead_score}",
                        severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                        entity_type="lead", entity_id=str(lead.id), entity_label=lead.name,
                        link=_link_for("lead", lead.id), status=lead.qualification_status,
                    ))

            # --- APPOINTMENT_CREATED --------------------------------------
            appointments = (
                await session.execute(
                    select(Appointment).where(Appointment.tenant_id == tenant_id)
                    .order_by(Appointment.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for appt in appointments:
                activities.append(Activity(
                    id=f"appointment_created:{appt.id}", timestamp=_aware(appt.created_at),
                    activity_type="APPOINTMENT_CREATED", category=CATEGORY_SALES,
                    title="Appointment booked", description=appt.title or "Appointment scheduled",
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="lead" if appt.lead_id else "customer",
                    entity_id=str(appt.lead_id or appt.customer_id) if (appt.lead_id or appt.customer_id) else None,
                    entity_label=None, link=_link_for("lead" if appt.lead_id else "customer", appt.lead_id or appt.customer_id),
                    status=appt.status,
                ))

            # --- QUOTE_CREATED / QUOTE_EXPIRED ------------------------------
            quotes = (
                await session.execute(
                    select(Quote).where(Quote.tenant_id == tenant_id).order_by(Quote.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for q in quotes:
                activities.append(Activity(
                    id=f"quote_created:{q.id}", timestamp=_aware(q.created_at), activity_type="QUOTE_CREATED",
                    category=CATEGORY_SALES, title=f"Quote {q.quote_number} created", description=f"${q.total}",
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="quote", entity_id=str(q.id), entity_label=q.quote_number,
                    link=_link_for("quote", q.id), status=q.status,
                ))
                if q.status == QuoteStatus.EXPIRED:
                    activities.append(Activity(
                        id=f"quote_expired:{q.id}", timestamp=_aware(q.updated_at), activity_type="QUOTE_EXPIRED",
                        category=CATEGORY_SALES, title=f"Quote {q.quote_number} expired",
                        description=f"${q.total}, no response before expiry.", severity="WARNING",
                        actor_type="system", actor_name=_actor_name("system"),
                        entity_type="quote", entity_id=str(q.id), entity_label=q.quote_number,
                        link=_link_for("quote", q.id), status=q.status,
                    ))

            # --- CONTRACT_CREATED / CONTRACT_SIGNED ----------------------
            contracts = (
                await session.execute(
                    select(Contract).where(Contract.tenant_id == tenant_id)
                    .order_by(Contract.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for c in contracts:
                activities.append(Activity(
                    id=f"contract_created:{c.id}", timestamp=_aware(c.created_at), activity_type="CONTRACT_CREATED",
                    category=CATEGORY_CONTRACT, title=f"Contract {c.contract_number} created", description="",
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="contract", entity_id=str(c.id), entity_label=c.contract_number,
                    link=_link_for("contract", c.id), status=c.status,
                ))
                if c.status == ContractStatus.SIGNED and c.decided_at is not None:
                    activities.append(Activity(
                        id=f"contract_signed:{c.id}", timestamp=_aware(c.decided_at), activity_type="CONTRACT_SIGNED",
                        category=CATEGORY_CONTRACT, title=f"Contract {c.contract_number} signed",
                        description=f"Signed by {c.signer_name}" if c.signer_name else "Signed",
                        severity="INFO", actor_type="human", actor_name=_actor_name("human"),
                        entity_type="contract", entity_id=str(c.id), entity_label=c.contract_number,
                        link=_link_for("contract", c.id), status=c.status,
                    ))

            # --- JOB_CREATED -----------------------------------------------
            jobs = (
                await session.execute(
                    select(Job).where(Job.tenant_id == tenant_id).order_by(Job.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            job_number_by_id = {j.id: j.job_number for j in jobs}
            for job in jobs:
                activities.append(Activity(
                    id=f"job_created:{job.id}", timestamp=_aware(job.created_at), activity_type="JOB_CREATED",
                    category=CATEGORY_OPERATIONS, title=f"Job {job.job_number} created", description=job.title,
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="job", entity_id=str(job.id), entity_label=job.job_number,
                    link=_link_for("job", job.id), status=job.status,
                ))

            # --- QA_FAILED (OperationsException) ----------------------------
            qa_failures = (
                await session.execute(
                    select(OperationsException).where(
                        OperationsException.tenant_id == tenant_id, OperationsException.type == ExceptionType.QA_FAILURE,
                    ).order_by(OperationsException.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for exc in qa_failures:
                job_number = job_number_by_id.get(exc.entity_id)
                activities.append(Activity(
                    id=f"qa_failed:{exc.id}", timestamp=_aware(exc.created_at), activity_type="QA_FAILED",
                    category=CATEGORY_QA, title=f"QA failed on job {job_number or exc.entity_id}",
                    description=exc.description, severity="ERROR", actor_type="system", actor_name=_actor_name("system"),
                    entity_type=exc.entity_type or "job", entity_id=str(exc.entity_id),
                    entity_label=job_number, link=_link_for(exc.entity_type or "job", exc.entity_id), status=exc.status,
                ))

            # --- QA_PASSED (JobQA) --------------------------------------------
            qa_passed_rows = (
                await session.execute(
                    select(JobQA).where(JobQA.tenant_id == tenant_id, JobQA.status == QAStatus.PASSED)
                    .order_by(JobQA.updated_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for qa in qa_passed_rows:
                job_number = job_number_by_id.get(qa.job_id)
                activities.append(Activity(
                    id=f"qa_passed:{qa.id}", timestamp=_aware(qa.updated_at), activity_type="QA_PASSED",
                    category=CATEGORY_QA, title=f"QA passed on job {job_number or qa.job_id}", description="",
                    severity="INFO", actor_type="human", actor_name=_actor_name("human"),
                    entity_type="job", entity_id=str(qa.job_id), entity_label=job_number,
                    link=_link_for("job", qa.job_id), status=qa.status,
                ))

            # --- SIGNOFF_COMPLETED ----------------------------------------------
            signoffs = (
                await session.execute(
                    select(CustomerSignoff).where(CustomerSignoff.tenant_id == tenant_id)
                    .order_by(CustomerSignoff.signed_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for s in signoffs:
                job_number = job_number_by_id.get(s.job_id)
                activities.append(Activity(
                    id=f"signoff_completed:{s.id}", timestamp=_aware(s.signed_at), activity_type="SIGNOFF_COMPLETED",
                    category=CATEGORY_QA, title=f"Customer signoff completed on job {job_number or s.job_id}",
                    description="", severity="INFO", actor_type="human", actor_name=_actor_name("human"),
                    entity_type="job", entity_id=str(s.job_id), entity_label=job_number,
                    link=_link_for("job", s.job_id), status="COMPLETED",
                ))

            # --- INVOICE_CREATED / INVOICE_OVERDUE ------------------------------
            invoices = (
                await session.execute(
                    select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for inv in invoices:
                activities.append(Activity(
                    id=f"invoice_created:{inv.id}", timestamp=_aware(inv.created_at), activity_type="INVOICE_CREATED",
                    category=CATEGORY_FINANCE, title=f"Invoice {inv.invoice_number} created", description=f"${inv.total}",
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="invoice", entity_id=str(inv.id), entity_label=inv.invoice_number,
                    link=_link_for("invoice", inv.id), status=inv.status,
                ))
                if inv.status == InvoiceStatus.OVERDUE:
                    activities.append(Activity(
                        id=f"invoice_overdue:{inv.id}", timestamp=_aware(inv.updated_at), activity_type="INVOICE_OVERDUE",
                        category=CATEGORY_FINANCE, title=f"Invoice {inv.invoice_number} became overdue",
                        description=f"${inv.amount_due} outstanding", severity="WARNING",
                        actor_type="system", actor_name=_actor_name("system"),
                        entity_type="invoice", entity_id=str(inv.id), entity_label=inv.invoice_number,
                        link=_link_for("invoice", inv.id), status=inv.status,
                    ))

            # --- PAYMENT_RECEIVED --------------------------------------------------
            payments = (
                await session.execute(
                    select(Payment).where(Payment.tenant_id == tenant_id).order_by(Payment.received_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for p in payments:
                activities.append(Activity(
                    id=f"payment_received:{p.id}", timestamp=_aware(p.received_at), activity_type="PAYMENT_RECEIVED",
                    category=CATEGORY_FINANCE, title="Payment received", description=f"${p.amount}",
                    severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="customer", entity_id=str(p.customer_id), entity_label=None,
                    link=_link_for("customer", p.customer_id), status=p.status,
                ))

            # --- RETENTION_OPPORTUNITY / REFERRAL_OPPORTUNITY ------------------------
            opportunities = (
                await session.execute(
                    select(RetentionOpportunity).where(RetentionOpportunity.tenant_id == tenant_id)
                    .order_by(RetentionOpportunity.detected_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for opp in opportunities:
                is_referral = opp.type == OpportunityType.REFERRAL_ELIGIBLE
                activities.append(Activity(
                    id=f"{'referral' if is_referral else 'retention'}_opportunity:{opp.id}",
                    timestamp=_aware(opp.detected_at),
                    activity_type="REFERRAL_OPPORTUNITY" if is_referral else "RETENTION_OPPORTUNITY",
                    category=CATEGORY_REFERRAL if is_referral else CATEGORY_RETENTION,
                    title=f"{'Referral' if is_referral else 'Retention'} opportunity: {opp.type}",
                    description=opp.reason, severity="INFO", actor_type="system", actor_name=_actor_name("system"),
                    entity_type="retention_opportunity", entity_id=str(opp.id), entity_label=None,
                    link=_link_for("retention_opportunity", opp.id), status=opp.status,
                ))

            # --- AUTOMATION_EXECUTED / AUTOMATION_FAILED -----------------------------
            executions = (
                await session.execute(
                    select(AutomationExecution).where(
                        AutomationExecution.tenant_id == tenant_id,
                        AutomationExecution.status.in_((ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)),
                    ).order_by(AutomationExecution.started_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for ex in executions:
                ts = ex.completed_at or ex.started_at
                failed = ex.status == ExecutionStatus.FAILED
                activities.append(Activity(
                    id=f"automation_{'failed' if failed else 'executed'}:{ex.id}", timestamp=_aware(ts),
                    activity_type="AUTOMATION_FAILED" if failed else "AUTOMATION_EXECUTED",
                    category=CATEGORY_AUTOMATION, title="Automation execution failed" if failed else "Automation executed",
                    description=(ex.error or "") if failed else "", severity="ERROR" if failed else "INFO",
                    actor_type="automation", actor_name=_actor_name("system"),
                    entity_type="automation_execution", entity_id=str(ex.id), entity_label=None,
                    link=_link_for("automation_execution", ex.id), status=ex.status,
                ))

            # --- AI_DECISION_PROPOSED / AI_ACTION_APPROVED --------------------------
            ai_approvals = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.requested_by_type == ActorType.AI,
                    ).order_by(ApprovalRequest.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for ar in ai_approvals:
                activities.append(Activity(
                    id=f"ai_decision_proposed:{ar.id}", timestamp=_aware(ar.created_at),
                    activity_type="AI_DECISION_PROPOSED", category=CATEGORY_AI,
                    title=f"Klaros AI proposed: {ar.action_type}", description=ar.reason,
                    severity="INFO", actor_type="ai", actor_name=_actor_name("ai"),
                    entity_type="approval_request", entity_id=str(ar.id), entity_label=None,
                    link=_link_for("approval_request", ar.id), status=ar.status,
                ))
                if ar.status == ApprovalStatus.APPROVED and ar.decided_at is not None:
                    activities.append(Activity(
                        id=f"ai_action_approved:{ar.id}", timestamp=_aware(ar.decided_at),
                        activity_type="AI_ACTION_APPROVED", category=CATEGORY_AI,
                        title=f"Owner approved AI action: {ar.action_type}", description=ar.decision_note or "",
                        severity="INFO", actor_type="human", actor_name=_actor_name("human"),
                        entity_type="approval_request", entity_id=str(ar.id), entity_label=None,
                        link=_link_for("approval_request", ar.id), status=ar.status,
                    ))

            # --- AI_ACTION_EXECUTED (AuditLog — the only reliable source that
            # covers BOTH the AUTO and the approved-then-executed paths) ----------
            ai_executions = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id, AuditLog.actor_type == ActorType.AI,
                        AuditLog.result == "success",
                        # Only real governed ToolRegistry calls (`tool` is set on
                        # every `tool.execute:*` audit row) — excludes internal
                        # service-level audit calls like `memory.propose` that
                        # aren't a "tool" execution and are already represented
                        # by their own dedicated activity type (AI_FEEDBACK_PENDING).
                        AuditLog.tool.is_not(None),
                    ).order_by(AuditLog.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for log in ai_executions:
                activities.append(Activity(
                    id=f"ai_action_executed:{log.id}", timestamp=_aware(log.created_at),
                    activity_type="AI_ACTION_EXECUTED", category=CATEGORY_AI,
                    title=f"Klaros AI executed: {log.tool}", description="",
                    severity="INFO", actor_type="ai", actor_name=_actor_name("ai"),
                    entity_type=log.entity_type, entity_id=str(log.entity_id) if log.entity_id else None,
                    entity_label=None, link=_link_for(log.entity_type, log.entity_id), status="success",
                ))

            # --- AI_FEEDBACK_PENDING / AI_FEEDBACK_CONFIRMED --------------------------
            feedback_rows = (
                await session.execute(
                    select(CompanyMemory).where(
                        CompanyMemory.tenant_id == tenant_id, CompanyMemory.memory_type == MemoryType.AI_FEEDBACK,
                        CompanyMemory.status.in_((MemoryStatus.PENDING, MemoryStatus.ACTIVE)),
                    ).order_by(CompanyMemory.created_at.desc()).limit(_PER_SOURCE_LIMIT)
                )
            ).scalars().all()
            for mem in feedback_rows:
                if mem.status == MemoryStatus.PENDING:
                    activities.append(Activity(
                        id=f"ai_feedback_pending:{mem.id}", timestamp=_aware(mem.created_at),
                        activity_type="AI_FEEDBACK_PENDING", category=CATEGORY_AI,
                        title="AI feedback awaiting review", description=mem.value,
                        severity="INFO", actor_type="ai", actor_name=_actor_name("ai"),
                        entity_type="company_memory", entity_id=str(mem.id), entity_label=None,
                        link=_link_for("company_memory", mem.id), status=mem.status,
                    ))
                else:
                    activities.append(Activity(
                        id=f"ai_feedback_confirmed:{mem.id}", timestamp=_aware(mem.updated_at),
                        activity_type="AI_FEEDBACK_CONFIRMED", category=CATEGORY_AI,
                        title="Owner confirmed AI feedback", description=mem.value,
                        severity="INFO", actor_type="human", actor_name=_actor_name("human"),
                        entity_type="company_memory", entity_id=str(mem.id), entity_label=None,
                        link=_link_for("company_memory", mem.id), status=mem.status,
                    ))

        return activities
