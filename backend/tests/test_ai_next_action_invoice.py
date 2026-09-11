"""Phase 20: the second AI Next Action scenario — overdue invoice
follow-up. Proves the Phase 18/19 governed decision pipeline generalizes
across an independent real business domain (invoices, not quotes) using
the SAME shared validation/execution/learning machinery, with no new
learning-specific code (Phase 19's hook keys off `requested_by_type ==
ActorType.AI` generically, not off any tool name).
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.ai_invocation import AIInvocationLog
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.automation import AutomationExecution, ExecutionStatus, TriggerType
from app.models.company_memory import CompanyMemory, MemoryStatus, MemoryType
from app.models.crm import Customer, CustomerStatus
from app.models.event import EventType
from app.models.finance import Invoice, InvoiceStatus
from app.models.notification import Notification
from app.models.rbac import Role
from app.models.tool_policy import TenantToolPolicy
from app.services.ai_next_action_service import AINextActionService
from app.services.ai_provider import AICallOutcome
from app.services.approval_execution_service import ApprovalExecutionService, ApprovalStateError
from app.services.automation_service import AutomationService
from app.services.company_memory_service import CompanyMemoryService
from app.services.policy_service import ActionPolicy
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_VALID_PROPOSAL = {
    "tool_name": "notifications.create_notification",
    "reason": "Invoice overdue with no payment — flagging for the owner to follow up.",
    "confidence": 0.75,
    "arguments": {"title": "Invoice overdue", "body": "An invoice is overdue with no payment recorded."},
}


class _FakeProvider:
    is_connected = True
    name = "fake"
    model = "fake-model-1"

    def __init__(self, payload: dict | str | None = None) -> None:
        self._payload = payload if payload is not None else _VALID_PROPOSAL
        self.last_prompt: str | None = None
        self.call_count = 0

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.last_prompt = prompt
        self.call_count += 1
        raw = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=raw)


class _DisconnectedProvider:
    is_connected = False
    name = "deterministic"
    model = "none"

    async def generate_structured(self, prompt: str) -> AICallOutcome:  # pragma: no cover
        raise AssertionError("must never be called when is_connected is False")


async def _seed_invoice(tenant_id: uuid.UUID, *, customer_name: str = "Overdue Invoice Customer") -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name=customer_name, status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number=f"INV-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=InvoiceStatus.OVERDUE, issue_date=date.today() - timedelta(days=40),
            due_date=date.today() - timedelta(days=20), currency="USD",
            subtotal=750, tax=0, discount=0, total=750, amount_due=750,
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        return invoice.id


def _ctx(tenant_id: uuid.UUID, correlation_id: uuid.UUID | None = None) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER, correlation_id=correlation_id)


async def _service(tool_registry, provider) -> AINextActionService:
    return AINextActionService(async_session_maker, AIExecutionService(tool_registry), provider)


# --- A/B: real business data reaches a valid decision -----------------

async def test_valid_proposal_on_auto_policy_executes(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)

    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())

    assert decision.outcome == "executed"
    assert "750" in provider.last_prompt  # B: real amount_due reached the prompt
    async with async_session_maker() as session:
        invoice = await session.get(Invoice, invoice_id)
    assert str(invoice.due_date) in provider.last_prompt

    async with async_session_maker() as session:
        rows = (await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1


async def test_invoice_not_found_is_honest(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, uuid.uuid4(), correlation_id=uuid.uuid4())
    assert decision.outcome == "invoice_not_found"
    assert provider.call_count == 0


# --- C/D: Company Memory + prompt fencing -----------------------------

async def test_company_memory_reaches_invoice_decision_prompt(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    memory = CompanyMemoryService(async_session_maker)
    await memory.create_memory(
        tenant_id, memory_type="OWNER_PREFERENCE", key="brand_tone", value="always calm and professional",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)

    await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    prompt = provider.last_prompt

    data_start = prompt.index("--- BEGIN BUSINESS DATA")
    data_end = prompt.index("--- END BUSINESS DATA")
    memory_start = prompt.index("--- BEGIN COMPANY MEMORY")
    memory_end = prompt.index("--- END COMPANY MEMORY")
    assert data_start < data_end < memory_start < memory_end  # D: fencing preserved
    assert "always calm and professional" in prompt[memory_start:memory_end]
    assert "always calm and professional" not in prompt[data_start:data_end]


# --- E/F: unknown / disallowed tool rejected ----------------------------

async def test_unknown_tool_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider({**_VALID_PROPOSAL, "tool_name": "finance.void_invoice"})
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "rejected"
    assert "tool_not_allowed" in decision.reason


async def test_disallowed_real_tool_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider({**_VALID_PROPOSAL, "tool_name": "finance.execute_due_collection_actions", "arguments": {}})
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "rejected"
    assert "tool_not_allowed" in decision.reason


# --- G/H: malformed output / invalid arguments rejected -----------------

async def test_malformed_output_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider("not json")
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "malformed_ai_output"


async def test_invalid_arguments_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider({**_VALID_PROPOSAL, "arguments": {"title": "x"}})  # missing "body"
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "rejected"
    assert "missing_required_arguments" in decision.reason


# --- I: identifier injection rejected -----------------------------------

async def test_identifier_injection_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider(
        {**_VALID_PROPOSAL, "arguments": {"title": "x", "body": "y", "invoice_id": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4())}}
    )
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "rejected"
    assert "unknown_arguments" in decision.reason
    async with async_session_maker() as session:
        rows = (await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))).scalars().all()
    assert rows == []


# --- J/K/L: policy outcomes ---------------------------------------------

async def test_auto_policy_executes_only_the_allowlisted_action(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "executed"
    assert decision.proposed_tool_name == "notifications.create_notification"


async def test_approval_required_creates_real_approval_no_early_execution(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    async with async_session_maker() as session:
        session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
        await session.commit()
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())

    assert decision.outcome == "approval_required"
    async with async_session_maker() as session:
        approval = await session.get(ApprovalRequest, decision.approval_request_id)
        assert approval.status == ApprovalStatus.PENDING
        rows = (await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))).scalars().all()
    assert rows == []


async def test_blocked_policy_produces_no_mutation(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    async with async_session_maker() as session:
        session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.BLOCKED, enabled=True))
        await session.commit()
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "denied"
    async with async_session_maker() as session:
        rows = (await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))).scalars().all()
    assert rows == []


# --- M: provider unavailable -> no proposal -----------------------------

async def test_disconnected_provider_produces_no_proposal(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    service = await _service(tool_registry, _DisconnectedProvider())
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "ai_unavailable"


# --- N: tenant isolation -------------------------------------------------

async def test_tenant_isolation(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    invoice_a = await _seed_invoice(tenant_a, customer_name="Tenant A Customer")
    memory = CompanyMemoryService(async_session_maker)
    await memory.create_memory(
        tenant_a, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tenant_a_only_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await memory.create_memory(
        tenant_b, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tenant_b_only_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    await service.decide_invoice_followup(tenant_a, invoice_a, correlation_id=uuid.uuid4())

    assert "tenant_a_only_marker" in provider.last_prompt
    assert "tenant_b_only_marker" not in provider.last_prompt
    assert "Tenant A Customer" in provider.last_prompt


# --- P/Q/R/S: execution recorded, audit, invocation log, correlation ----

async def test_execution_audit_and_invocation_log_recorded_with_shared_correlation(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    correlation_id = uuid.uuid4()
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=correlation_id)
    assert decision.outcome == "executed"

    async with async_session_maker() as session:
        invocation = (
            await session.execute(
                select(AIInvocationLog).where(
                    AIInvocationLog.tenant_id == tenant_id, AIInvocationLog.correlation_id == correlation_id,
                    AIInvocationLog.operation == "ai_next_action_invoice_followup",
                )
            )
        ).scalar_one()
        assert invocation.success is True
        assert invocation.input_metadata["invoice_id"] == str(invoice_id)

        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.correlation_id == correlation_id,
                    AuditLog.tool == "notifications.create_notification",
                )
            )
        ).scalar_one()
        assert audit.result == "success"
        assert audit.actor_type == ActorType.AI


# --- T/U: Phase 19 learning reused, no new implementation ----------------

async def test_phase19_learning_works_for_invoice_scenario_without_new_code(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)
    async with async_session_maker() as session:
        session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
        await session.commit()
    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "approval_required"

    approvals = ApprovalExecutionService(async_session_maker, tool_registry, event_bus)
    owner_id = uuid.uuid4()
    await approvals.approve(tenant_id, decision.approval_request_id, decided_by_id=owner_id, decided_by_role=Role.OWNER)

    async with async_session_maker() as session:
        memories = (
            await session.execute(
                select(CompanyMemory).where(CompanyMemory.tenant_id == tenant_id, CompanyMemory.memory_type == MemoryType.AI_FEEDBACK)
            )
        ).scalars().all()
    assert len(memories) == 1
    feedback = memories[0]
    assert feedback.status == MemoryStatus.PENDING
    assert feedback.source == "AI_PROPOSED"
    assert feedback.source_entity_type == "approval_request"
    assert feedback.source_entity_id == decision.approval_request_id
    assert "notifications.create_notification" in feedback.value

    # U: confirming it (existing, unchanged human action) makes it reach a
    # brand-new, independent invoice decision.
    company_memory = CompanyMemoryService(async_session_maker)
    await company_memory.confirm_memory(tenant_id, feedback.id, confirmed_by=uuid.uuid4())

    new_invoice_id = await _seed_invoice(tenant_id)
    new_provider = _FakeProvider()
    new_service = await _service(tool_registry, new_provider)
    await new_service.decide_invoice_followup(tenant_id, new_invoice_id, correlation_id=uuid.uuid4())
    assert "Owner APPROVED an AI-proposed action" in new_provider.last_prompt


# --- V/W: Phase 18/19 quote behavior + learning remain unchanged --------

async def test_quote_followup_still_works_unchanged(tool_registry) -> None:
    from app.models.quote import Quote, QuoteStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Still Works Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"Q-STILL-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=QuoteStatus.EXPIRED, currency="USD", subtotal=500, tax=0, discount=0, total=500,
            valid_until=date.today() - timedelta(days=1),
        )
        session.add(quote)
        await session.commit()
        quote_id = quote.id

    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_quote_followup(tenant_id, quote_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "executed"


async def test_quote_approval_learning_still_works_unchanged(tool_registry, event_bus) -> None:
    from app.models.quote import Quote, QuoteStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Still Learns Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"Q-LEARN-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=QuoteStatus.EXPIRED, currency="USD", subtotal=500, tax=0, discount=0, total=500,
            valid_until=date.today() - timedelta(days=1),
        )
        session.add(quote)
        session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
        await session.commit()
        quote_id = quote.id

    provider = _FakeProvider()
    service = await _service(tool_registry, provider)
    decision = await service.decide_quote_followup(tenant_id, quote_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "approval_required"

    approvals = ApprovalExecutionService(async_session_maker, tool_registry, event_bus)
    await approvals.reject(tenant_id, decision.approval_request_id, decided_by_id=uuid.uuid4(), note="Not needed.")

    async with async_session_maker() as session:
        memories = (
            await session.execute(
                select(CompanyMemory).where(CompanyMemory.tenant_id == tenant_id, CompanyMemory.memory_type == MemoryType.AI_FEEDBACK)
            )
        ).scalars().all()
    assert len(memories) == 1
    assert "REJECTED" in memories[0].value
    assert "Not needed." in memories[0].value


# --- Rule 20: full real end-to-end scenario, through the Automation Engine -

async def test_end_to_end_invoice_overdue_through_automation_to_ai_decision(monkeypatch) -> None:
    fake_provider = _FakeProvider()
    monkeypatch.setattr("app.services.ai_provider.get_ai_provider", lambda: fake_provider)

    from app.ai.execution_service import AIExecutionService
    from app.events.automation_handlers import register_automation_handlers
    from app.events.bus import EventBus
    from app.events.handlers import register_default_handlers
    from app.events.operations_handlers import register_operations_handlers
    from app.events.transport import InMemoryTransport
    from app.tools.factory import build_tool_registry

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_default_handlers(bus)
    register_operations_handlers(bus, async_session_maker)
    registry = build_tool_registry(async_session_maker, bus)
    register_automation_handlers(bus, async_session_maker, AIExecutionService(registry))
    automation_service = AutomationService(async_session_maker, AIExecutionService(registry))

    customer_result = await registry.execute("crm.create_customer", {"name": "E2E Invoice Customer", "email": "invoice-e2e@example.com"}, ctx)
    async with async_session_maker() as session:
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number=f"INV-E2E-{uuid.uuid4().hex[:8]}", customer_id=uuid.UUID(customer_result.customer["id"]),
            status=InvoiceStatus.SENT, issue_date=date.today() - timedelta(days=40),
            due_date=date.today() - timedelta(days=20), currency="USD", total=750, amount_due=750,
            sent_at=datetime.now(timezone.utc) - timedelta(days=40),
        )
        session.add(invoice)
        await session.commit()
        invoice_id = invoice.id

    sweep_automation = await automation_service.create_automation(
        tenant_id, name="Daily Overdue Invoice Sweep", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "08:00"}, condition=None,
        steps=[{"action": "finance.detect_overdue_invoices", "params": {}}], created_by=None,
    )
    await automation_service.publish(tenant_id, sweep_automation.id)

    decide_automation = await automation_service.create_automation(
        tenant_id, name="AI Decides Invoice Followup", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.EXCEPTION_CREATED},
        condition={"field": "invoice.type", "op": "eq", "value": "INVOICE_OVERDUE"},
        steps=[{"action": "ai.propose_invoice_followup", "params": {"invoice_id": "{{event.entity_id}}"}}],
        created_by=None,
    )
    await automation_service.publish(tenant_id, decide_automation.id)

    dispatched = await automation_service.check_and_dispatch_scheduled(
        tenant_id, now_utc=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    )
    assert len(dispatched) == 1

    async with async_session_maker() as session:
        invoice_row = await session.get(Invoice, invoice_id)
        assert invoice_row.status == InvoiceStatus.OVERDUE

    stats = await bus.process_pending(EventType.EXCEPTION_CREATED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        decide_executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == decide_automation.id))
        ).scalars().all()
    assert len(decide_executions) == 1
    assert decide_executions[0].status == ExecutionStatus.COMPLETED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Invoice overdue"))
        ).scalars().all()
    assert len(notifications) == 1
    assert fake_provider.call_count == 1


# --- Real HTTP: invoice scenario, honest no-credential + full approve/learn loop -

async def test_real_http_invoice_followup_honest_and_learn_loop(monkeypatch) -> None:
    """Real HTTP against the actual FastAPI app. Two parts:
    1. The honest no-live-credential path: create+publish a real EVENT
       automation via HTTP, manually trigger it over HTTP (no live LLM key
       -> real 'ai_unavailable' outcome, no fabricated execution).
    2. The full Phase 19 learn loop, reused unchanged for this second
       scenario: a test-provider-backed proposal -> real HTTP approve ->
       real HTTP GET /memory shows one AI_FEEDBACK row -> real HTTP
       confirm -> a brand-new decision consumes it. No invoice-specific
       learning code exists anywhere — this is the SAME hook Phase 19
       built for quotes, proven to cover invoices with zero changes.
    There is no HTTP endpoint to create an Invoice directly (only via a
    real job's trigger-from-job lifecycle) — the invoice/customer rows
    are seeded directly, exactly like Phase 11's own Overdue Invoice
    worked-example test does; every governance-relevant step below (the
    automation, the trigger, the approval, the memory) is real HTTP.
    """
    from httpx import ASGITransport, AsyncClient

    from app.api.tool_deps import get_tool_registry, get_wired_event_bus
    from app.core.config import get_settings
    from app.events.bus import EventBus
    from app.events.handlers import register_default_handlers
    from app.events.transport import InMemoryTransport
    from app.main import app
    from app.tools.factory import build_tool_registry

    _settings = get_settings()

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_default_handlers(bus)
    registry = build_tool_registry(async_session_maker, bus)
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_wired_event_bus] = lambda: bus

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post(
                "/api/v1/auth/register",
                json={"organization_name": "Phase20 HTTP Co", "full_name": "Owner", "email": "phase20-http@example.com", "password": "supersecret1"},
            )
            assert reg.status_code == 201, reg.text
            token = reg.json()["tokens"]["access_token"]
            tenant_id = uuid.UUID(reg.json()["user"]["tenant_id"])
            headers = {"Authorization": f"Bearer {token}"}

            invoice_id = await _seed_invoice(tenant_id)

            # --- Part 1: real automation + honest no-credential trigger ---
            automation = await client.post(
                "/api/v1/automations",
                json={
                    "name": "AI Decides Invoice Followup (HTTP)", "trigger_type": "EVENT",
                    "trigger_config": {"event_type": "exception.created"},
                    "condition": {"field": "invoice.type", "op": "eq", "value": "INVOICE_OVERDUE"},
                    "steps": [{"action": "ai.propose_invoice_followup", "params": {"invoice_id": "{{event.entity_id}}"}}],
                },
                headers=headers,
            )
            assert automation.status_code == 201, automation.text
            automation_id = automation.json()["id"]
            await client.post(f"/api/v1/automations/{automation_id}/publish", headers=headers)

            triggered = await client.post(
                f"/api/v1/automations/{automation_id}/trigger",
                json={
                    "entity_type": "invoice", "entity_id": str(invoice_id),
                    "context": {"event": {"entity_id": str(invoice_id)}, "invoice": {"type": "INVOICE_OVERDUE"}},
                },
                headers=headers,
            )
            assert triggered.status_code == 200, triggered.text
            assert triggered.json()["status"] == "COMPLETED"

            execution_detail = await client.get(f"/api/v1/automations/executions/{triggered.json()['id']}", headers=headers)
            step_result = execution_detail.json()["steps"][0]["result"]
            # Phase 31: `build_tool_registry()` wires this tool to the REAL
            # `get_ai_provider()` factory (never a test double) — so this
            # assertion must be honest about WHICHEVER environment is
            # actually running: "ai_unavailable" when no live credential is
            # configured (the original, still-valid case this test was
            # written for), or any real, valid outcome when one now is
            # (this repo has since gained a live OPENAI_API_KEY in some
            # environments — Phase 31's own live certification). Never
            # asserting a specific live outcome here (the model's raw
            # output isn't controlled by this test) — only that the
            # pipeline behaved honestly either way, matching the same
            # tolerant outcome set `test_live_ai_provider.py` already uses.
            live_provider_configured = bool(_settings.ANTHROPIC_API_KEY or _settings.OPENAI_API_KEY)
            if live_provider_configured:
                assert step_result["outcome"] in (
                    "executed", "approval_required", "denied", "rejected",
                    "no_action_proposed", "malformed_ai_output", "ai_call_failed",
                )
            else:
                assert step_result["outcome"] == "ai_unavailable"  # honest — no live LLM credential in this sandbox

            notifications = await client.get("/api/v1/notifications", headers=headers)
            if not live_provider_configured or step_result["outcome"] != "executed":
                assert notifications.json()["notifications"] == []

            # --- Part 2: the full approve -> learn -> future-decision loop,
            # using the SAME real HTTP approval/memory endpoints Phase 19
            # already proved for quotes — zero new code for invoices. ---
            async with async_session_maker() as session:
                session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
                await session.commit()

            fake_provider = _FakeProvider()
            decision_service = AINextActionService(async_session_maker, AIExecutionService(registry), fake_provider)
            decision = await decision_service.decide_invoice_followup(tenant_id, invoice_id, correlation_id=uuid.uuid4())
            assert decision.outcome == "approval_required"
            approval_id = decision.approval_request_id

            approval_detail = await client.get(f"/api/v1/approvals/{approval_id}", headers=headers)
            assert approval_detail.json()["status"] == "PENDING"

            approve_resp = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={}, headers=headers)
            assert approve_resp.status_code == 200, approve_resp.text

            memories = await client.get("/api/v1/memory", params={"memory_type": "AI_FEEDBACK"}, headers=headers)
            assert len(memories.json()["memories"]) == 1
            feedback = memories.json()["memories"][0]
            assert feedback["status"] == "PENDING"
            assert "APPROVED" in feedback["value"]
            assert "notifications.create_notification" in feedback["value"]

            confirm_resp = await client.post(f"/api/v1/memory/{feedback['id']}/confirm", json={}, headers=headers)
            assert confirm_resp.status_code == 200, confirm_resp.text
            assert confirm_resp.json()["status"] == "ACTIVE"

            new_invoice_id = await _seed_invoice(tenant_id)
            new_provider = _FakeProvider()
            new_decision_service = AINextActionService(async_session_maker, AIExecutionService(registry), new_provider)
            await new_decision_service.decide_invoice_followup(tenant_id, new_invoice_id, correlation_id=uuid.uuid4())
            assert "Owner APPROVED an AI-proposed action" in new_provider.last_prompt
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)
        app.dependency_overrides.pop(get_wired_event_bus, None)
