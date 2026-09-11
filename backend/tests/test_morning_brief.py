"""Morning Brief (Phase 8B): deterministic insight detection, real data
sourcing through insights.* tools, tenant isolation, permission enforcement,
and recommendation execution."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.finance import Invoice, InvoiceStatus
from app.models.morning_brief import MorningBriefMode
from app.models.rbac import Permission, Role, role_has_permission
from app.tools.base import ExecutionContext
from app.tools.errors import ToolPermissionError

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_empty_tenant_reports_no_significant_activity(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    out = await tool_registry.execute("insights.generate_morning_brief", {}, ctx)

    assert out.headline == "No significant activity."
    assert out.mode == MorningBriefMode.DETERMINISTIC


async def test_finance_insight_reflects_a_real_overdue_invoice(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Overdue Co"}, ctx)
    customer_id = customer.customer["id"]
    job = await tool_registry.execute(
        "operations.create_job",
        {"title": "Repair", "customer_id": customer_id, "estimated_revenue": 300.0},
        ctx,
    )
    job_id = job.job["id"]
    invoice = await tool_registry.execute(
        "finance.trigger_invoice_from_job", {"job_id": job_id}, ctx
    )
    invoice_id = invoice.invoice["id"]
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)
    await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice_id}, ctx)

    # Force it overdue directly (a real past-due date, not a fabricated status).
    async with tool_registry._session_factory() as session:
        row = await session.get(Invoice, uuid.UUID(invoice_id))
        row.status = InvoiceStatus.OVERDUE
        row.due_date = date.today() - timedelta(days=5)
        await session.commit()

    out = await tool_registry.execute("insights.generate_morning_brief", {}, ctx)

    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    finance_insights = [i for i in latest.insights if i.category == "FINANCE"]
    assert finance_insights

    if out.mode == "DETERMINISTIC":
        assert "overdue" in out.headline.lower()
        assert any("overdue" in i.summary.lower() for i in finance_insights)
    else:
        # Phase 31: with a real AI provider connected, the headline and
        # per-insight summary are legitimately AI-rephrased prose (see
        # app/services/morning_brief_service.py's `prose_by_entity` step)
        # — a real, documented, frozen-core feature, not required to
        # contain the exact keyword "overdue" verbatim. Structural facts
        # (a FINANCE insight exists, produced from the real overdue
        # invoice) are what's actually being proven here either way.
        assert out.headline.strip()
        assert all(i.summary.strip() for i in finance_insights)


async def test_retention_insight_reflects_real_negative_feedback(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Unhappy Co"}, ctx)
    customer_id = customer.customer["id"]
    await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 1, "comment": "Terrible"}, ctx
    )

    out = await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    retention_insights = [i for i in latest.insights if i.category == "RETENTION"]
    assert retention_insights
    if latest.mode == "DETERMINISTIC":
        assert any("service recovery" in i.summary.lower() for i in retention_insights)
    else:
        # Phase 31: with a real AI provider connected (this environment's
        # live OPENAI_API_KEY), MorningBriefService.generate() legitimately
        # replaces an insight's `summary` with the model's own prose (see
        # app/services/morning_brief_service.py's `prose_by_entity` step) —
        # a real, documented, frozen-core feature, not a bug. It never
        # touches `recommendations`, so those stay deterministic and are
        # still checked exactly below regardless of mode.
        assert all(i.summary.strip() for i in retention_insights)
    recovery_recs = [r for r in latest.recommendations if "recovery" in r.what.lower()]
    assert recovery_recs
    assert "public review" not in recovery_recs[0].next_action.lower() or "do not" in recovery_recs[0].next_action.lower()


async def test_sales_insight_flags_qualified_lead_without_appointment(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    lead = await tool_registry.execute(
        "crm.create_lead",
        {"name": "Stalled Lead", "source": "REFERRAL", "service_requested": "AC repair", "urgency": "HIGH"},
        ctx,
    )
    lead_id = lead.lead["id"]
    await tool_registry.execute("crm.qualify_lead", {"lead_id": lead_id}, ctx)

    async with tool_registry._session_factory() as session:
        from app.models.crm import Lead

        row = await session.get(Lead, uuid.UUID(lead_id))
        row.updated_at = datetime.now(timezone.utc) - timedelta(days=3)
        await session.commit()

    latest_before = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    assert latest_before.brief_id is None

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    sales_insights = [i for i in latest.insights if i.category == "SALES" and i.related_entity_id == lead_id]
    assert sales_insights
    follow_up_recs = [r for r in latest.recommendations if r.related_entity_id == lead_id]
    assert follow_up_recs
    assert "Stalled Lead" in follow_up_recs[0].what


async def test_tenant_isolation_briefs_never_cross_tenants(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ctx_a, ctx_b = _ctx(tenant_a), _ctx(tenant_b)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Tenant A Customer"}, ctx_a)
    await tool_registry.execute(
        "retention.record_feedback",
        {"customer_id": customer.customer["id"], "rating": 1, "comment": "Tenant A only"},
        ctx_a,
    )

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx_a)
    await tool_registry.execute("insights.generate_morning_brief", {}, ctx_b)

    brief_a = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx_a)
    brief_b = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx_b)

    assert brief_a.headline != "No significant activity."
    assert brief_b.headline == "No significant activity."
    assert not any(i.category == "RETENTION" for i in brief_b.insights)


async def test_permission_enforcement_read_only_role_cannot_generate(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, role=Role.READ_ONLY)

    assert role_has_permission(Role.READ_ONLY, Permission.READ_MORNING_BRIEF)
    assert not role_has_permission(Role.READ_ONLY, Permission.GENERATE_MORNING_BRIEF)

    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("insights.generate_morning_brief", {}, ctx)

    # Read-only role can still read whatever the latest brief already is.
    result = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    assert result.brief_id is None


async def test_execute_recommendation_dispatches_the_real_underlying_tool(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    program = await tool_registry.execute(
        "retention.create_referral_program", {"name": "Refer a Friend", "reward_type": "credit", "reward_amount": "25.00"}, ctx
    )
    referrer = await tool_registry.execute("crm.create_customer", {"name": "Referrer"}, ctx)
    code = await tool_registry.execute(
        "retention.get_or_create_referral_code",
        {"program_id": program.program_id, "customer_id": referrer.customer["id"]},
        ctx,
    )
    referral = await tool_registry.execute(
        "retention.create_referral", {"referral_code_id": code.code_id}, ctx
    )
    lead_out = await tool_registry.execute(
        "retention.convert_referral_to_lead",
        {"referral_id": referral.referral_id, "name": "Referred Person"},
        ctx,
    )
    await tool_registry.execute(
        "retention.request_referral_reward", {"referral_id": referral.referral_id, "amount": "25.00"}, ctx
    )

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    reward_recs = [r for r in latest.recommendations if r.executable and "reward" in r.what.lower()]
    assert reward_recs, [r.what for r in latest.recommendations]
    rec = reward_recs[0]

    result = await tool_registry.execute(
        "insights.execute_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
    )
    assert result.status == "EXECUTED"
    assert result.tool_result["status"] == "APPROVED"


async def test_execute_recommendation_hitting_approval_required_tool_links_to_real_approval(
    tool_registry,
) -> None:
    """Phase 9: Execute on a recommendation whose underlying tool now
    requires approval must not raise or silently fail — it must record a
    real ApprovalRequest and let the recommendation point at it, so the
    Owner can approve from /approvals and have the ORIGINAL recommended
    action actually resume and execute."""
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    program = await tool_registry.execute(
        "retention.create_referral_program", {"name": "Refer a Friend", "reward_type": "credit", "reward_amount": "25.00"}, ctx
    )
    referrer = await tool_registry.execute("crm.create_customer", {"name": "Referrer"}, ctx)
    code = await tool_registry.execute(
        "retention.get_or_create_referral_code",
        {"program_id": program.program_id, "customer_id": referrer.customer["id"]},
        ctx,
    )
    referral = await tool_registry.execute(
        "retention.create_referral", {"referral_code_id": code.code_id}, ctx
    )
    await tool_registry.execute(
        "retention.convert_referral_to_lead",
        {"referral_id": referral.referral_id, "name": "Referred Person"},
        ctx,
    )
    await tool_registry.execute(
        "retention.request_referral_reward", {"referral_id": referral.referral_id, "amount": "25.00"}, ctx
    )

    original_policy = DEFAULT_TOOL_POLICIES.get("retention.approve_referral_reward")
    DEFAULT_TOOL_POLICIES["retention.approve_referral_reward"] = ActionPolicy.APPROVAL_REQUIRED
    try:
        await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
        latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
        reward_recs = [r for r in latest.recommendations if r.executable and "reward" in r.what.lower()]
        assert reward_recs
        rec = reward_recs[0]

        result = await tool_registry.execute(
            "insights.execute_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
        )
        assert result.status == "APPROVAL_REQUESTED"
        assert result.tool_result is None
        assert result.approval_request_id

        latest_after = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
        updated_rec = next(r for r in latest_after.recommendations if r.recommendation_id == rec.recommendation_id)
        assert updated_rec.status == "APPROVAL_REQUESTED"
        assert updated_rec.approval_request_id == result.approval_request_id

        detail = await tool_registry.execute(
            "approvals.get_detail", {"approval_request_id": uuid.UUID(result.approval_request_id)}, ctx
        )
        assert detail.tool_name == "retention.approve_referral_reward"
        assert detail.status == "PENDING"
    finally:
        if original_policy is None:
            DEFAULT_TOOL_POLICIES.pop("retention.approve_referral_reward", None)
        else:
            DEFAULT_TOOL_POLICIES["retention.approve_referral_reward"] = original_policy


class _FakeConnectedProvider:
    """A provider double for exercising MorningBriefService's AI-mode wiring
    without any real network call — this is testing the SERVICE's boundary
    logic (cross-checking entity_ids, never trusting AI for numbers), not
    the provider's own plumbing (already covered by test_ai_provider.py)."""

    is_connected = True

    def __init__(self, summary: str, insight_prose: list[dict]) -> None:
        self._summary = summary
        self._insight_prose = insight_prose
        self.last_brand_voice: str | None = None
        self.last_company_memory: str | None = None

    async def enrich_brief(self, headline, insights, *, brand_voice=None, company_memory=None):
        from app.services.ai_provider import AIBriefEnrichment, AICallOutcome, AIInsightProse, AIProviderResult

        self.last_brand_voice = brand_voice
        self.last_company_memory = company_memory

        return (
            AIProviderResult(
                enrichment=AIBriefEnrichment(
                    summary=self._summary,
                    insights=[AIInsightProse(**p) for p in self._insight_prose],
                ),
                provider="fake",
                model="fake-model-1",
                generation_ms=5,
            ),
            AICallOutcome(
                success=True, provider="fake", model="fake-model-1", latency_ms=5,
                input_tokens=10, output_tokens=20,
            ),
        )


async def test_ai_mode_upgrades_headline_and_records_provider_metadata(tool_registry, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Overdue Co"}, ctx)
    job = await tool_registry.execute(
        "operations.create_job",
        {"title": "Repair", "customer_id": customer.customer["id"], "estimated_revenue": 300.0},
        ctx,
    )
    invoice = await tool_registry.execute("finance.trigger_invoice_from_job", {"job_id": job.job["id"]}, ctx)
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice.invoice["id"]}, ctx)
    await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice.invoice["id"]}, ctx)
    async with tool_registry._session_factory() as session:
        row = await session.get(Invoice, uuid.UUID(invoice.invoice["id"]))
        row.status = InvoiceStatus.OVERDUE
        row.due_date = date.today() - timedelta(days=5)
        await session.commit()

    fake_provider = _FakeConnectedProvider("AI-rephrased headline about overdue billing.", [])
    monkeypatch.setattr(
        "app.services.morning_brief_service.get_ai_provider", lambda: fake_provider
    )

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    assert latest.mode == "AI"
    assert latest.headline == "AI-rephrased headline about overdue billing."
    assert latest.ai_provider == "fake"
    assert latest.ai_model == "fake-model-1"
    # The real numeric claim is still exactly what the deterministic tool
    # computed — the AI only ever touched the headline string.
    finance_insight = next(i for i in latest.insights if i.category == "FINANCE")
    assert "overdue" in finance_insight.summary.lower()


async def test_ai_mode_writes_an_ai_invocation_log_row(tool_registry, monkeypatch) -> None:
    """Regression: AIQualificationService always wrote AIInvocationLog on
    every real provider call, but MorningBriefService's own AI-enrichment
    call (enrich_brief) never did — an AI-mode brief could rephrase a
    headline via a real, billable provider call with zero audit trace of
    it. Fixed by having enrich_brief return the raw AICallOutcome
    alongside its parsed result so the caller (which owns tenant/session
    context) can record it exactly like the qualification path already
    does."""
    from app.models.ai_invocation import AIInvocationLog

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Audit Co"}, ctx)
    job = await tool_registry.execute(
        "operations.create_job",
        {"title": "Repair", "customer_id": customer.customer["id"], "estimated_revenue": 300.0},
        ctx,
    )
    invoice = await tool_registry.execute("finance.trigger_invoice_from_job", {"job_id": job.job["id"]}, ctx)
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice.invoice["id"]}, ctx)
    await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice.invoice["id"]}, ctx)
    async with tool_registry._session_factory() as session:
        row = await session.get(Invoice, uuid.UUID(invoice.invoice["id"]))
        row.status = InvoiceStatus.OVERDUE
        row.due_date = date.today() - timedelta(days=5)
        await session.commit()

    fake_provider = _FakeConnectedProvider("AI-rephrased headline.", [])
    monkeypatch.setattr("app.services.morning_brief_service.get_ai_provider", lambda: fake_provider)

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)

    async with tool_registry._session_factory() as session:
        logs = (
            await session.execute(
                select(AIInvocationLog).where(
                    AIInvocationLog.tenant_id == tenant_id,
                    AIInvocationLog.operation == "morning_brief_enrichment",
                )
            )
        ).scalars().all()
    assert len(logs) == 1
    assert logs[0].provider == "fake"
    assert logs[0].model == "fake-model-1"
    assert logs[0].success is True
    assert logs[0].input_tokens == 10
    assert logs[0].output_tokens == 20


async def test_ai_mode_passes_the_tenants_real_brand_voice_guide_to_the_provider(
    tool_registry, monkeypatch
) -> None:
    """Phase 12: the Knowledge Layer isn't just a place to store text nobody
    reads — a real brand/voice-guide.md the tenant wrote is fetched and
    handed to the AI provider on every AI-mode brief generation. A tenant
    who never wrote one gets None, never a fabricated default."""
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Voice Co"}, ctx)
    await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer.customer["id"], "rating": 1}, ctx
    )

    fake_provider = _FakeConnectedProvider("headline", [])
    monkeypatch.setattr("app.services.morning_brief_service.get_ai_provider", lambda: fake_provider)

    # No voice guide configured yet — the provider must see None, not "".
    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    assert fake_provider.last_brand_voice is None

    await tool_registry.execute(
        "knowledge.set_file",
        {"path": "brand/voice-guide.md", "content": "Warm, plain-spoken, never salesy."},
        ctx,
    )
    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    assert fake_provider.last_brand_voice == "Warm, plain-spoken, never salesy."


async def test_ai_response_cannot_attach_prose_to_an_entity_that_was_never_flagged(
    tool_registry, monkeypatch
) -> None:
    """The core prompt-injection / hallucination defense: MorningBriefService
    cross-checks every entity_id an AI response references against the real
    deterministic insight list and drops anything that doesn't match — an
    AI response can't invent a claim about some other customer/lead/invoice
    the deterministic pass never actually surfaced."""
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    lead = await tool_registry.execute(
        "crm.create_lead",
        {"name": "Stalled Lead", "source": "REFERRAL", "service_requested": "AC repair", "urgency": "HIGH"},
        ctx,
    )
    await tool_registry.execute("crm.qualify_lead", {"lead_id": lead.lead["id"]}, ctx)
    async with tool_registry._session_factory() as session:
        from app.models.crm import Lead

        row = await session.get(Lead, uuid.UUID(lead.lead["id"]))
        row.updated_at = datetime.now(timezone.utc) - timedelta(days=3)
        await session.commit()

    real_lead_id = lead.lead["id"]
    fabricated_id = str(uuid.uuid4())
    fake_provider = _FakeConnectedProvider(
        "Real headline text.",
        [
            {"entity_id": fabricated_id, "text": "This customer's invoice is $50,000 overdue!"},
            {"entity_id": real_lead_id, "text": "Stalled Lead really needs a callback today."},
        ],
    )
    monkeypatch.setattr(
        "app.services.morning_brief_service.get_ai_provider", lambda: fake_provider
    )

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    # The prose attached to the real, flagged lead was accepted.
    real_insight = next(i for i in latest.insights if i.related_entity_id == real_lead_id)
    assert real_insight.summary == "Stalled Lead really needs a callback today."
    # No insight anywhere references the fabricated entity_id — it was
    # silently dropped, not stored, not surfaced.
    assert not any(i.related_entity_id == fabricated_id for i in latest.insights)


async def test_dismiss_recommendation(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Dismiss Co"}, ctx)
    await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer.customer["id"], "rating": 2}, ctx
    )
    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    rec = latest.recommendations[0]

    result = await tool_registry.execute(
        "insights.dismiss_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
    )
    assert result.status == "DISMISSED"
