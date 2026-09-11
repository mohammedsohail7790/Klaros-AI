"""Phase 26: real end-to-end proof for the Owner Attention Queue —
`GET /api/v1/dashboard/attention`. Builds one realistic mixed business
state (lead, qualified lead, appointment, quote, pending contract, active
job, QA failure, overdue invoice, retention opportunity, referral
opportunity, automation execution, AI approval, AI feedback) entirely
through real ORM rows / real HTTP where an endpoint exists, then asserts
on the REAL response from the REAL endpoint — no mocked dashboard
response, no hardcoded expected frontend data.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.automation import AutomationExecution, ExecutionStatus, TriggerType
from app.models.company_memory import CompanyMemory, MemorySource, MemoryStatus, MemoryType
from app.models.contract import Contract, ContractStatus
from app.models.crm import Appointment, AppointmentStatus, Customer, CustomerStatus, Lead, LeadStatus, QualificationStatus
from app.models.finance import Invoice, InvoiceStatus
from app.models.operations import ExceptionStatus, ExceptionType, Job, JobStatus, OperationsException
from app.models.quote import Quote, QuoteStatus
from app.models.retention import OpportunityStatus, OpportunityType, RetentionOpportunity

pytestmark = pytest.mark.asyncio


async def _create_second_user(tenant_id: uuid.UUID, *, role) -> str:
    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id, email=f"{uuid.uuid4().hex[:8]}@phase26.com",
            hashed_password=hash_password("supersecret1"), full_name="Second User", role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return create_access_token(user.id, tenant_id, role.value)


async def _register(client: AsyncClient, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Phase26 Owner Co", "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["tokens"]["access_token"], uuid.UUID(data["user"]["tenant_id"])


async def _seed_business_state(tenant_id: uuid.UUID) -> dict:
    now = datetime.now(timezone.utc)
    ids = {}
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Attention Queue Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        ids["customer_id"] = customer.id

        # Plain unqualified lead (not everything should be qualified)
        lead1 = Lead(tenant_id=tenant_id, name="Fresh Lead", source="WEBSITE", email="fresh@example.com")
        session.add(lead1)

        # Qualified lead with NO appointment -> should appear in the queue
        lead2 = Lead(
            tenant_id=tenant_id, name="Qualified No Appt", source="WEBSITE", email="qna@example.com",
            qualification_status=QualificationStatus.QUALIFIED, status=LeadStatus.CONTACTED,
            updated_at=now - timedelta(days=3),
        )
        session.add(lead2)
        await session.flush()
        ids["qualified_lead_id"] = lead2.id

        # Qualified lead WITH an appointment -> should NOT appear
        lead3 = Lead(
            tenant_id=tenant_id, name="Qualified With Appt", source="WEBSITE", email="qwa@example.com",
            qualification_status=QualificationStatus.QUALIFIED, status=LeadStatus.CONTACTED,
        )
        session.add(lead3)
        await session.flush()
        appt = Appointment(
            tenant_id=tenant_id, lead_id=lead3.id, customer_id=customer.id, title="Estimate visit",
            start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=1),
            status=AppointmentStatus.CONFIRMED,
        )
        session.add(appt)

        # Stale quote (sent 10 days ago, no response)
        quote = Quote(
            tenant_id=tenant_id, quote_number="Q-OWNER-1", customer_id=customer.id, status=QuoteStatus.SENT,
            currency="USD", subtotal=2000, tax=0, discount=0, total=Decimal("2000.00"),
            sent_at=now - timedelta(days=10),
        )
        session.add(quote)
        await session.flush()
        ids["quote_id"] = quote.id

        # Pending contract (sent 5 days ago)
        contract = Contract(
            tenant_id=tenant_id, contract_number="CTR-OWNER-1", quote_id=quote.id, customer_id=customer.id,
            status=ContractStatus.SENT, content="Agreement text", content_hash="x" * 64,
            sent_at=now - timedelta(days=5),
        )
        session.add(contract)
        await session.flush()
        ids["contract_id"] = contract.id

        # Active job with a QA failure exception
        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, job_number="JOB-OWNER-1", title="Owner Job",
            status=JobStatus.QA_PENDING,
        )
        session.add(job)
        await session.flush()
        ids["job_id"] = job.id
        qa_exc = OperationsException(
            tenant_id=tenant_id, type=ExceptionType.QA_FAILURE, severity="HIGH", entity_type="job",
            entity_id=job.id, description=f"QA failed for job {job.job_number}: loose wiring",
            recommended_action="Re-inspect and fix.", status=ExceptionStatus.OPEN,
        )
        session.add(qa_exc)

        # Overdue invoice
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number="INV-OWNER-1", customer_id=customer.id,
            status=InvoiceStatus.OVERDUE, issue_date=date.today() - timedelta(days=20),
            due_date=date.today() - timedelta(days=6), currency="USD",
            subtotal=Decimal("1500.00"), tax=0, discount=0, total=Decimal("1500.00"),
            amount_paid=0, amount_due=Decimal("1500.00"),
        )
        session.add(invoice)
        await session.flush()
        ids["invoice_id"] = invoice.id

        # Retention opportunity (non-referral)
        retention_opp = RetentionOpportunity(
            tenant_id=tenant_id, customer_id=customer.id, type=OpportunityType.POST_JOB_FOLLOWUP,
            reason="Job closed, no follow-up sent yet.", detected_at=now - timedelta(days=2),
            status=OpportunityStatus.OPEN, source_event="job.closed",
        )
        session.add(retention_opp)

        # Referral opportunity
        referral_opp = RetentionOpportunity(
            tenant_id=tenant_id, customer_id=customer.id, type=OpportunityType.REFERRAL_ELIGIBLE,
            reason="Customer left great feedback.", detected_at=now - timedelta(days=1),
            status=OpportunityStatus.OPEN, source_event="retention.review_received",
        )
        session.add(referral_opp)

        # AI approval pending
        approval = ApprovalRequest(
            tenant_id=tenant_id, requested_by_type=ActorType.AI, tool_name="notifications.create_notification",
            action_type="notifications.create_notification", reason="AI proposed a follow-up notification.",
            tool_input={"title": "x", "body": "y"}, status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        await session.flush()
        ids["approval_id"] = approval.id

        # AI feedback pending review
        memory = CompanyMemory(
            tenant_id=tenant_id, memory_type=MemoryType.AI_FEEDBACK, key=f"ai_feedback_owner_test_{uuid.uuid4().hex}",
            value="Owner approved an AI-proposed action: tool='notifications.create_notification'.",
            source=MemorySource.AI_PROPOSED, status=MemoryStatus.PENDING, confidence=0.8,
        )
        session.add(memory)
        await session.flush()
        ids["memory_id"] = memory.id

        # Failed automation execution
        from app.models.automation import Automation, AutomationVersion

        automation = Automation(tenant_id=tenant_id, name="Owner Test Automation")
        session.add(automation)
        await session.flush()
        version = AutomationVersion(
            tenant_id=tenant_id, automation_id=automation.id, version_number=1, trigger_type=TriggerType.EVENT,
            trigger_config={"event_type": "job.qa_failed"}, condition=None,
            steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        )
        session.add(version)
        await session.flush()
        execution = AutomationExecution(
            tenant_id=tenant_id, automation_id=automation.id, automation_version_id=version.id,
            trigger_type=TriggerType.EVENT, status=ExecutionStatus.FAILED, context={},
            started_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=2), error="tool execution failed",
        )
        session.add(execution)
        await session.flush()
        ids["execution_id"] = execution.id

        await session.commit()
    return ids


async def test_attention_queue_aggregates_real_mixed_business_state(client: AsyncClient) -> None:
    token, tenant_id = await _register(client, "phase26-owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    ids = await _seed_business_state(tenant_id)

    resp = await client.get("/api/v1/dashboard/attention", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    items = data["items"]

    categories_seen = {i["category"] for i in items}
    expected_categories = {
        "qualified_lead_no_appointment", "quote_stale", "contract_pending", "job_qa_failed",
        "invoice_overdue", "retention_opportunity", "referral_opportunity",
        "ai_approval_required", "ai_feedback_pending", "automation_failed",
    }
    assert expected_categories.issubset(categories_seen), f"missing: {expected_categories - categories_seen}"

    # The qualified lead WITH an appointment must never appear.
    lead_entity_ids = {i["entity_id"] for i in items if i["entity_type"] == "lead"}
    assert str(ids["qualified_lead_id"]) in lead_entity_ids
    # lead3 (with appointment) was never captured in ids on purpose — verify
    # by count: exactly one lead-category item (the one without an appointment).
    assert len(lead_entity_ids) == 1

    # Every item must be sorted by score descending.
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)

    # Every item must carry enough context to act (WHAT/WHY/entity/link).
    for item in items:
        assert item["title"]
        assert item["reason"]
        assert item["entity_type"]
        assert item["entity_id"]
        assert item["link"].startswith("/")
        assert item["priority"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")

    # Real monetary values surfaced, not fabricated.
    invoice_items = [i for i in items if i["category"] == "invoice_overdue"]
    assert invoice_items[0]["monetary_value"] == "1500.00"
    assert invoice_items[0]["entity_id"] == str(ids["invoice_id"])

    quote_items = [i for i in items if i["category"] == "quote_stale"]
    assert quote_items[0]["monetary_value"] == "2000.00"

    # QA failure links back to the real job.
    qa_items = [i for i in items if i["category"] == "job_qa_failed"]
    assert qa_items[0]["entity_id"] == str(ids["job_id"])
    assert qa_items[0]["link"] == f"/jobs/{ids['job_id']}"

    # Counts are real, derived server-side, not client-computed.
    assert data["critical_count"] + data["high_count"] <= len(items)
    assert data["critical_count"] == sum(1 for i in items if i["priority"] == "CRITICAL")
    assert data["high_count"] == sum(1 for i in items if i["priority"] == "HIGH")


async def test_attention_queue_empty_tenant_returns_empty_not_fabricated(client: AsyncClient) -> None:
    token, tenant_id = await _register(client, "phase26-empty@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/v1/dashboard/attention", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["critical_count"] == 0
    assert data["high_count"] == 0


async def test_attention_queue_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/dashboard/attention")
    assert resp.status_code in (401, 403)


async def test_ai_health_reports_honest_unconfigured_state(client: AsyncClient) -> None:
    """`provider_configured` must always match the real environment's
    credential state, never a fixed assumption — no live LLM credential
    existed when this test was originally written (Phase 22's own
    finding), but this environment has since gained a real, live
    OPENAI_API_KEY (Phase 31). Honest either way: a brand-new tenant with
    zero AI activity always has zero invocation counts regardless."""
    from app.core.config import get_settings

    token, _ = await _register(client, "phase26-aihealth@example.com")
    resp = await client.get("/api/v1/dashboard/ai-health", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    live_provider_configured = bool(get_settings().ANTHROPIC_API_KEY or get_settings().OPENAI_API_KEY)
    assert data["provider_configured"] is live_provider_configured
    assert data["invocations_24h"] == 0
    assert data["invocations_24h_succeeded"] == 0
    assert data["invocations_24h_failed"] == 0


async def test_ai_health_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/dashboard/ai-health")
    assert resp.status_code in (401, 403)


async def test_ai_health_tenant_isolation(client: AsyncClient) -> None:
    from app.ai.execution_service import AIExecutionService
    from app.models.ai_invocation import AIInvocationLog

    token_a, tenant_a = await _register(client, "phase26-ai-tenant-a@example.com")
    token_b, tenant_b = await _register(client, "phase26-ai-tenant-b@example.com")

    async with async_session_maker() as session:
        session.add(AIInvocationLog(
            tenant_id=tenant_a, actor_type="ai", provider="test", model="test-model",
            operation="test_op", success=True, latency_ms=5,
        ))
        await session.commit()

    resp_b = await client.get("/api/v1/dashboard/ai-health", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.json()["invocations_24h"] == 0  # tenant B never sees tenant A's real invocation log row

    resp_a = await client.get("/api/v1/dashboard/ai-health", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.json()["invocations_24h"] == 1


async def test_attention_queue_tenant_isolation(client: AsyncClient) -> None:
    token_a, tenant_a = await _register(client, "phase26-tenant-a@example.com")
    await _seed_business_state(tenant_a)
    token_b, tenant_b = await _register(client, "phase26-tenant-b@example.com")

    resp_b = await client.get("/api/v1/dashboard/attention", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    assert resp_b.json()["items"] == []  # tenant B sees none of tenant A's real attention items

    resp_a = await client.get("/api/v1/dashboard/attention", headers={"Authorization": f"Bearer {token_a}"})
    assert len(resp_a.json()["items"]) > 0


async def test_attention_queue_readonly_and_technician_can_view(client: AsyncClient) -> None:
    """Step 15: viewing the attention queue is a read, not a mutation, so
    every real role (including READ_ONLY and TECHNICIAN) can see it —
    mirrors the exact `_create_second_user` pattern Phase 21's own RBAC
    test uses."""
    from app.models.rbac import Role

    _, tenant_id = await _register(client, "phase26-rbac-owner@example.com")
    await _seed_business_state(tenant_id)

    for role in (Role.READ_ONLY, Role.TECHNICIAN, Role.MANAGER):
        token = await _create_second_user(tenant_id, role=role)
        resp = await client.get("/api/v1/dashboard/attention", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, f"{role} could not view attention queue: {resp.text}"
        assert len(resp.json()["items"]) > 0
