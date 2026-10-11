"""Phase 8B: the Owner AI Morning Brief.

Every number in a generated brief comes from a real `insights.*` Tool call
made through `AIExecutionService` (actor_type=AI) — never a direct query
against the database from this service. `MorningBriefService` only
*synthesizes* — turns structured tool output into insight/recommendation
rows using deterministic, named thresholds (the same "rule-based detection,
not an LLM guess" pattern used for lead scoring, exception detection, and
feedback sentiment in earlier phases).

`mode` is DETERMINISTIC unless a real LLM provider is configured (see
app/services/ai_provider.py) — checked in code, not just asserted, so this
can never silently claim to be "AI-written" prose when it's actually a
template.
"""

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.execution_service import AIExecutionService, ToolRequest
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.morning_brief import (
    InsightCategory,
    InsightPriority,
    MorningBrief,
    MorningBriefInsight,
    MorningBriefMode,
    MorningBriefRecommendation,
    RecommendationStatus,
)
from app.models.organization import Organization
from app.models.rbac import Role
from app.services.ai_boundary import bound_ai_provider, bound_embedding_provider
from app.services.ai_invocation_log_service import record_ai_invocation
from app.services.ai_provider import get_ai_provider
from app.services.company_memory_service import CompanyMemoryService, format_context_as_text
from app.services.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)

# Thresholds are named constants, not magic numbers scattered through the
# synthesis logic below — mirrors the deterministic-detection pattern used
# for retention/marketing exceptions in earlier phases.
STALE_QUALIFIED_LEAD_DAYS = 2


class MorningBriefService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        ai_execution_service: AIExecutionService,
        bus=None,
    ) -> None:
        self._session_factory = session_factory
        self._ai = ai_execution_service
        # Optional (Phase 10B): lets generate() publish MORNING_BRIEF_GENERATED
        # so app/events/notification_handlers.py can create one grouped
        # notification. None is fine — nothing else here depends on it.
        self._bus = bus
        # Phase 12: real Knowledge Layer integration (see generate() below).
        self._knowledge = KnowledgeService(session_factory)
        # Phase 13: real Company Memory integration (see generate() below).
        self._memory = CompanyMemoryService(session_factory)

    async def _call(self, tool_name: str, tenant_id: uuid.UUID):
        return await self._ai.request_tool_execution(
            ToolRequest(tool_name=tool_name, input={}),
            tenant_id=tenant_id,
            ai_role=Role.MANAGER,
        )

    async def generate(
        self, tenant_id: uuid.UUID, *, generated_by: ActorType = ActorType.USER
    ) -> dict:
        finance = await self._call("insights.get_finance_snapshot", tenant_id)
        operations = await self._call("insights.get_operations_snapshot", tenant_id)
        sales = await self._call("insights.get_sales_snapshot", tenant_id)
        commercial_pipeline = await self._call("insights.get_commercial_pipeline_snapshot", tenant_id)
        marketing = await self._call("insights.get_marketing_snapshot", tenant_id)
        retention = await self._call("insights.get_retention_snapshot", tenant_id)
        exceptions = await self._call("insights.get_exception_snapshot", tenant_id)
        voice = await self._call("insights.get_voice_snapshot", tenant_id)

        provider = get_ai_provider()
        mode = MorningBriefMode.DETERMINISTIC  # only upgraded below if a real provider call actually succeeds

        insights: list[dict] = []
        recommendations: list[dict] = []

        # --- Finance ---
        if finance.overdue_invoice_count > 0:
            insights.append(
                {
                    "category": InsightCategory.FINANCE,
                    "priority": InsightPriority.HIGH if finance.pending_approval_count else InsightPriority.MEDIUM,
                    "summary": f"{finance.overdue_invoice_count} invoice(s) overdue, ${finance.total_ar} total AR outstanding.",
                    "source_tool": "insights.get_finance_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": "Review overdue invoices",
                    "why": f"{finance.overdue_invoice_count} invoice(s) are past due.",
                    "related_entity_type": "invoice",
                    "related_entity_id": None,
                    "next_action": "Open AR and review collection status.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )
        if float(finance.collected_last_24h) > 0:
            insights.append(
                {
                    "category": InsightCategory.FINANCE,
                    "priority": InsightPriority.LOW,
                    "summary": f"${finance.collected_last_24h} collected from {finance.payments_last_24h_count} payment(s) in the last 24 hours.",
                    "source_tool": "insights.get_finance_snapshot",
                }
            )

        # --- Operations ---
        if operations.blocked_jobs_count > 0:
            insights.append(
                {
                    "category": InsightCategory.OPERATIONS,
                    "priority": InsightPriority.HIGH,
                    "summary": f"{operations.blocked_jobs_count} job(s) are currently blocked.",
                    "source_tool": "insights.get_operations_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": "Unblock stalled jobs",
                    "why": f"{operations.blocked_jobs_count} job(s) have been blocked.",
                    "related_entity_type": "job",
                    "related_entity_id": None,
                    "next_action": "Open Operations and review blocked jobs.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )
        if operations.jobs_closed_last_24h > 0:
            insights.append(
                {
                    "category": InsightCategory.OPERATIONS,
                    "priority": InsightPriority.LOW,
                    "summary": f"{operations.jobs_closed_last_24h} job(s) closed in the last 24 hours.",
                    "source_tool": "insights.get_operations_snapshot",
                }
            )
        if operations.unassigned_scheduled_jobs_count > 0:
            insights.append(
                {
                    "category": InsightCategory.OPERATIONS,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"{operations.unassigned_scheduled_jobs_count} scheduled job(s) have no assigned worker.",
                    "source_tool": "insights.get_operations_snapshot",
                }
            )

        # --- Sales ---
        if sales.qualified_leads_awaiting_appointment:
            for lead in sales.qualified_leads_awaiting_appointment[:5]:
                days = lead.get("qualified_days_ago", 0)
                insights.append(
                    {
                        "category": InsightCategory.SALES,
                        "priority": InsightPriority.HIGH if days >= STALE_QUALIFIED_LEAD_DAYS else InsightPriority.MEDIUM,
                        "summary": f"Qualified lead '{lead['name']}' has no appointment ({days} day(s) since qualification).",
                        "related_entity_type": "lead",
                        "related_entity_id": lead["lead_id"],
                        "source_tool": "insights.get_sales_snapshot",
                    }
                )
                recommendations.append(
                    {
                        "what": f"Follow up with {lead['name']}",
                        "why": f"Qualified {days} day(s) ago but no appointment exists.",
                        "related_entity_type": "lead",
                        "related_entity_id": lead["lead_id"],
                        "next_action": "Book an appointment or send a follow-up.",
                        "executable_tool": None,
                        "executable_input": None,
                    }
                )
        if sales.new_leads_today or sales.appointments_today:
            insights.append(
                {
                    "category": InsightCategory.SALES,
                    "priority": InsightPriority.LOW,
                    "summary": f"{sales.new_leads_today} new lead(s) today, {sales.appointments_today} appointment(s) scheduled today.",
                    "source_tool": "insights.get_sales_snapshot",
                }
            )

        # --- Commercial pipeline (Quote -> Contract -> Deposit) ---
        for contract in commercial_pipeline.contracts_awaiting_signature[:5]:
            insights.append(
                {
                    "category": InsightCategory.SALES,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"Contract {contract['contract_number']} is {contract['status'].lower()}, awaiting signature.",
                    "related_entity_type": "contract",
                    "related_entity_id": contract["contract_id"],
                    "source_tool": "insights.get_commercial_pipeline_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": f"Contract {contract['contract_number']} needs signature",
                    "why": "The quote was accepted but the agreement remains unsigned.",
                    "related_entity_type": "contract",
                    "related_entity_id": contract["contract_id"],
                    "next_action": "Send a contract reminder or follow up with the customer.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )
        for quote in commercial_pipeline.deposits_awaiting_payment[:5]:
            insights.append(
                {
                    "category": InsightCategory.FINANCE,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"Deposit of ${quote['deposit_amount']} for quote {quote['quote_number']} is still outstanding.",
                    "related_entity_type": "quote",
                    "related_entity_id": quote["quote_id"],
                    "source_tool": "insights.get_commercial_pipeline_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": f"Deposit outstanding for {quote['quote_number']}",
                    "why": "The customer accepted the quote but has not yet paid the deposit.",
                    "related_entity_type": "quote",
                    "related_entity_id": quote["quote_id"],
                    "next_action": "Send a payment reminder.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )

        for quote in commercial_pipeline.stale_quotes_awaiting_response[:5]:
            insights.append(
                {
                    "category": InsightCategory.SALES,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"Quote {quote['quote_number']} (${quote['total']}) has had no customer response since it was sent.",
                    "related_entity_type": "quote",
                    "related_entity_id": quote["quote_id"],
                    "source_tool": "insights.get_commercial_pipeline_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": f"Quote {quote['quote_number']} is stale, awaiting response",
                    "why": "The quote was sent but the customer has neither accepted nor declined it within the configured follow-up window.",
                    "related_entity_type": "quote",
                    "related_entity_id": quote["quote_id"],
                    "next_action": "Follow up with the customer to check on the quote.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )

        # --- AI Voice Receptionist ---
        if voice.calls_today > 0:
            insights.append(
                {
                    "category": InsightCategory.SALES,
                    "priority": InsightPriority.LOW,
                    "summary": (
                        f"{voice.calls_today} call(s) handled by the AI receptionist in the last 24h, "
                        f"{voice.new_leads_from_voice_today} new lead(s) generated."
                    ),
                    "source_tool": "insights.get_voice_snapshot",
                }
            )
        if voice.human_handoffs_today > 0:
            insights.append(
                {
                    "category": InsightCategory.SALES,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"{voice.human_handoffs_today} caller(s) requested a human callback in the last 24h.",
                    "source_tool": "insights.get_voice_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": f"{voice.human_handoffs_today} voice call(s) awaiting human callback",
                    "why": "The AI receptionist could not fully resolve these callers and flagged them for a real person.",
                    "related_entity_type": None,
                    "related_entity_id": None,
                    "next_action": "Review recent calls and call these customers back.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )
        if voice.unresolved_calls_today > 0:
            insights.append(
                {
                    "category": InsightCategory.SALES,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"{voice.unresolved_calls_today} call(s) ended unresolved in the last 24h.",
                    "source_tool": "insights.get_voice_snapshot",
                }
            )

        # --- Marketing ---
        if marketing.leads_last_24h > 0 or float(marketing.spend_last_30d) > 0:
            insights.append(
                {
                    "category": InsightCategory.MARKETING,
                    "priority": InsightPriority.LOW,
                    "summary": (
                        f"{marketing.active_campaign_count} active campaign(s), "
                        f"${marketing.spend_last_30d} spent in the last 30 days, "
                        f"{marketing.leads_last_24h} lead(s) in the last 24 hours."
                    ),
                    "source_tool": "insights.get_marketing_snapshot",
                }
            )

        # --- Retention ---
        for opp in retention.open_retention_opportunities_detail:
            insights.append(
                {
                    "category": InsightCategory.RETENTION,
                    "priority": InsightPriority.LOW,
                    "summary": opp["reason"],
                    "related_entity_type": "customer",
                    "related_entity_id": opp["customer_id"],
                    "source_tool": "insights.get_retention_snapshot",
                }
            )
            recommendations.append(
                {
                    "what": opp["reason"],
                    "why": f"Detected retention opportunity ({opp['type']}).",
                    "related_entity_type": "customer",
                    "related_entity_id": opp["customer_id"],
                    "next_action": opp["recommended_action"] or "Review this customer's retention opportunity.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )
        for review in retention.eligible_review_requests:
            recommendations.append(
                {
                    "what": "Send a review request",
                    "why": "A recently closed job is eligible for a review request that hasn't been sent yet.",
                    "related_entity_type": "customer",
                    "related_entity_id": review["customer_id"],
                    "next_action": "Send the review request through the existing review workflow.",
                    "executable_tool": "retention.send_review_request",
                    "executable_input": {"review_request_id": review["review_request_id"]},
                }
            )
        for fb in retention.reviews_awaiting_marketing_consent:
            recommendations.append(
                {
                    "what": f"Ask for permission to use a {fb['rating']}/5 review publicly",
                    "why": "This review is positive enough to be marketing content, but the customer "
                    "hasn't been asked for consent yet — never used publicly without it.",
                    "related_entity_type": "customer",
                    "related_entity_id": fb["customer_id"],
                    "next_action": "Ask the customer for permission, then record their answer.",
                    "executable_tool": None,
                    "executable_input": None,
                }
            )
        for fb in retention.reviews_ready_for_marketing_content:
            recommendations.append(
                {
                    "what": f"Turn a consented {fb['rating']}/5 review into marketing content",
                    "why": "The customer explicitly consented to their review being used publicly.",
                    "related_entity_type": "customer",
                    "related_entity_id": fb["customer_id"],
                    "next_action": "Create a content idea from this review through the existing content pipeline.",
                    "executable_tool": "marketing.create_content_from_review",
                    "executable_input": {"feedback_id": fb["feedback_id"]},
                }
            )
        if retention.negative_feedback_last_24h:
            for fb in retention.negative_feedback_last_24h[:5]:
                insights.append(
                    {
                        "category": InsightCategory.RETENTION,
                        "priority": InsightPriority.HIGH,
                        "summary": f"Negative feedback (rating {fb['rating']}/5) requires service recovery.",
                        "related_entity_type": "customer",
                        "related_entity_id": fb["customer_id"],
                        "source_tool": "insights.get_retention_snapshot",
                    }
                )
                recommendations.append(
                    {
                        "what": "Perform service recovery",
                        "why": f"Customer left a rating of {fb['rating']}/5.",
                        "related_entity_type": "customer",
                        "related_entity_id": fb["customer_id"],
                        "next_action": "Contact the customer directly — do not send a public review request.",
                        "executable_tool": None,
                        "executable_input": None,
                    }
                )
        if retention.at_risk_customers > 0:
            insights.append(
                {
                    "category": InsightCategory.RETENTION,
                    "priority": InsightPriority.MEDIUM,
                    "summary": f"{retention.at_risk_customers} customer(s) are at risk of churning.",
                    "source_tool": "insights.get_retention_snapshot",
                }
            )
        if retention.pending_referral_rewards:
            for reward in retention.pending_referral_rewards[:5]:
                recommendations.append(
                    {
                        "what": "Approve pending referral reward",
                        "why": f"${reward['amount']} reward has been pending since the referral converted.",
                        "related_entity_type": "referral_reward",
                        "related_entity_id": reward["reward_id"],
                        "next_action": "Review and approve the reward.",
                        "executable_tool": "retention.approve_referral_reward",
                        "executable_input": {"reward_id": reward["reward_id"]},
                    }
                )

        # --- Exceptions ---
        if exceptions.open_count > 0:
            insights.append(
                {
                    "category": InsightCategory.EXCEPTIONS,
                    "priority": InsightPriority.HIGH if exceptions.high_severity_count else InsightPriority.MEDIUM,
                    "summary": f"{exceptions.open_count} open exception(s), {exceptions.high_severity_count} high severity.",
                    "source_tool": "insights.get_exception_snapshot",
                }
            )

        if insights:
            top = sorted(insights, key=lambda i: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[i["priority"]])[0]
            headline = top["summary"]
        else:
            headline = "No significant activity."

        ai_provider_name: str | None = None
        ai_model: str | None = None
        ai_generation_ms: int | None = None

        if provider.is_connected and insights:
            # entity_ids actually present in the real, already-computed
            # insight list — the only ids an AI response is ever allowed to
            # reference. Anything else is dropped, not trusted.
            real_entity_ids = {i["related_entity_id"] for i in insights if i.get("related_entity_id")}

            # Phase 12: real Knowledge Layer integration — if the tenant has
            # written a brand/voice-guide.md, the AI's rephrasing actually
            # follows it. None (never configured) is passed straight
            # through; the provider treats a missing guide as "no style
            # guidance," never fabricates one.
            brand_voice = await self._knowledge.get_content(tenant_id, "brand/voice-guide.md")
            # Phase 13: real Company Memory integration — bounded, active,
            # in-effect owner preferences/context (never PENDING/REVOKED/
            # REJECTED/ARCHIVED, never expired, never another tenant's).
            # Empty list -> None, same "no fabricated guidance" honesty as
            # brand_voice above.
            company_memory = format_context_as_text(await self._memory.get_context(tenant_id))
            result, call_outcome = await bound_ai_provider(provider, self._session_factory, tenant_id, "morning_brief").enrich_brief(
                headline, insights, brand_voice=brand_voice, company_memory=company_memory,
            )
            if call_outcome is not None:
                await record_ai_invocation(
                    self._session_factory,
                    tenant_id=tenant_id,
                    actor_type=generated_by,
                    actor_id=None,
                    operation="morning_brief_enrichment",
                    outcome=call_outcome,
                    input_metadata={"insight_count": len(insights)},
                )
            if result is not None:
                enrichment = result.enrichment
                # The AI may only ever replace *prose* — it can rephrase the
                # headline and per-insight summaries. It can never add, remove,
                # or reorder insights/recommendations, and never touches any
                # number, id, or status; those all stay exactly as computed
                # above regardless of what the response contains.
                if enrichment.summary.strip():
                    headline = enrichment.summary.strip()
                    mode = MorningBriefMode.AI
                    ai_provider_name = result.provider
                    ai_model = result.model
                    ai_generation_ms = result.generation_ms

                prose_by_entity = {
                    p.entity_id: p.text.strip()
                    for p in enrichment.insights
                    if p.entity_id and p.entity_id in real_entity_ids and p.text.strip()
                }
                if prose_by_entity:
                    for ins in insights:
                        eid = ins.get("related_entity_id")
                        if eid in prose_by_entity:
                            ins["summary"] = prose_by_entity[eid]
                    mode = MorningBriefMode.AI
                    ai_provider_name = ai_provider_name or result.provider
                    ai_model = ai_model or result.model
                    ai_generation_ms = ai_generation_ms if ai_generation_ms is not None else result.generation_ms

        now = datetime.now(timezone.utc)
        source_data = {
            "finance": finance.model_dump(),
            "operations": operations.model_dump(),
            "sales": sales.model_dump(),
            "commercial_pipeline": commercial_pipeline.model_dump(),
            "marketing": marketing.model_dump(),
            "retention": retention.model_dump(),
            "exceptions": exceptions.model_dump(),
        }

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            brief = MorningBrief(
                tenant_id=tenant_id,
                brief_date=now.date(),
                generated_at=now,
                mode=mode,
                generated_by=generated_by,
                headline=headline,
                source_data=source_data,
                ai_provider=ai_provider_name,
                ai_model=ai_model,
                ai_generation_ms=ai_generation_ms,
            )
            session.add(brief)
            await session.flush()

            for ins in insights:
                raw_id = ins.get("related_entity_id")
                session.add(
                    MorningBriefInsight(
                        tenant_id=tenant_id,
                        brief_id=brief.id,
                        category=ins["category"],
                        priority=ins["priority"],
                        summary=ins["summary"],
                        related_entity_type=ins.get("related_entity_type"),
                        related_entity_id=uuid.UUID(raw_id) if raw_id else None,
                        source_tool=ins.get("source_tool"),
                    )
                )
            for rec in recommendations:
                raw_id = rec.get("related_entity_id")
                session.add(
                    MorningBriefRecommendation(
                        tenant_id=tenant_id,
                        brief_id=brief.id,
                        what=rec["what"],
                        why=rec["why"],
                        related_entity_type=rec.get("related_entity_type"),
                        related_entity_id=uuid.UUID(raw_id) if raw_id else None,
                        next_action=rec["next_action"],
                        executable_tool=rec.get("executable_tool"),
                        executable_input=rec.get("executable_input"),
                        status=RecommendationStatus.PENDING,
                    )
                )
            await session.commit()
            await session.refresh(brief)
            brief_id = brief.id

        logger.info(
            "morning_brief_generated",
            tenant_id=str(tenant_id),
            mode=mode,
            generated_by=generated_by,
            insight_count=len(insights),
            recommendation_count=len(recommendations),
        )

        if self._bus is not None and recommendations:
            from app.models.event import EventType

            await self._bus.publish(
                tenant_id=tenant_id,
                event_type=EventType.MORNING_BRIEF_GENERATED,
                source="morning_brief_service",
                entity_type="morning_brief",
                entity_id=brief_id,
                payload={
                    "headline": headline,
                    "recommendation_count": len(recommendations),
                    "high_priority_insight_count": sum(1 for i in insights if i["priority"] == InsightPriority.HIGH),
                },
            )

        return {"brief_id": str(brief_id), "headline": headline, "mode": mode}

    async def check_and_generate_scheduled(self) -> None:
        """Called once per Event Worker tick (see app/events/worker.py's
        `on_tick` hook) — piggybacks on the already-running poll loop
        instead of adding a second sleep-based scheduler. Idempotent: only
        generates a brief for a tenant if today's (tenant-local) brief
        doesn't already exist, so running this every tick is safe.

        Phase 17B-2R classification, refined by Phase 17B-3: tenant
        *discovery* here is a genuine CROSS_TENANT_SYSTEM scan — finding
        which orgs have `morning_brief_enabled` requires reading across
        every tenant, and there is no single tenant_id to scope that
        query to (see `PHASE_17B3_SYSTEM_GLOBAL_CONTEXT_IMPLEMENTATION_LOG.md`
        for the full design rationale). That discovery session below reads
        ONLY `Organization` and sets no tenant context, by design — it is
        never used to read or write any other, genuinely tenant-owned
        table. Every subsequent per-tenant check (the `MorningBrief`
        existence lookup, a genuinely tenant-scoped table) and every
        per-tenant mutation (`self.generate`) now runs in its OWN,
        separately-opened session with `set_tenant_context(session, org.id)`
        called first — tenant-by-tenant iteration, not a single
        cross-tenant session touching tenant-owned rows."""
        async with self._session_factory() as session:  # CROSS_TENANT_SYSTEM discovery — Organization only, see docstring
            orgs = (
                await session.execute(select(Organization).where(Organization.morning_brief_enabled.is_(True)))
            ).scalars().all()

        due: list[uuid.UUID] = []
        for org in orgs:
            try:
                tz = ZoneInfo(org.morning_brief_timezone)
            except Exception:  # noqa: BLE001 — an invalid tz string must never crash the worker loop
                tz = ZoneInfo("UTC")
            local_now = datetime.now(tz)
            try:
                hour_str, minute_str = org.morning_brief_local_time.split(":")
                target = local_now.replace(hour=int(hour_str), minute=int(minute_str), second=0, microsecond=0)
            except (ValueError, IndexError):
                continue
            if local_now < target:
                continue
            async with self._session_factory() as session:
                await set_tenant_context(session, org.id)
                existing = (
                    await session.execute(
                        select(MorningBrief).where(
                            MorningBrief.tenant_id == org.id, MorningBrief.brief_date == local_now.date()
                        )
                    )
                ).scalar_one_or_none()
            if existing is None:
                due.append(org.id)

        for tenant_id in due:
            try:
                await self.generate(tenant_id, generated_by=ActorType.SYSTEM)
            except Exception as exc:  # noqa: BLE001 — one tenant's failure must never block others
                logger.error("scheduled_morning_brief_failed", tenant_id=str(tenant_id), error=str(exc))
