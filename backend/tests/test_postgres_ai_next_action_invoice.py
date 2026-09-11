"""Phase 20: real PostgreSQL verification for the second AI Next Action
scenario (overdue invoice follow-up) — tenant isolation and duplicate-
event concurrency, mirroring test_postgres_ai_next_action.py's evidence
for the first (quote) scenario. No new idempotency mechanism: this is the
SAME real `AutomationExecution` unique constraint on
(automation_version_id, source_event_id), proven generically in
test_postgres_automation_concurrency.py and specifically for AI Next
Action in Phase 18's test_postgres_ai_next_action.py.
"""

import asyncio
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.ai_invocation import AIInvocationLog
from app.models.crm import Customer, CustomerStatus
from app.models.finance import Invoice, InvoiceStatus
from app.models.notification import Notification
from app.services.ai_provider import AICallOutcome
from app.services.automation_service import AutomationService
from app.tools.factory import build_tool_registry

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_VALID_PROPOSAL = {
    "tool_name": "notifications.create_notification",
    "reason": "Invoice overdue with no payment.",
    "confidence": 0.75,
    "arguments": {"title": "Invoice overdue", "body": "An invoice is overdue."},
}


class _FakeProvider:
    is_connected = True
    name = "fake"
    model = "fake-model-1"

    def __init__(self) -> None:
        self.call_count = 0

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.call_count += 1
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=json.dumps(_VALID_PROPOSAL))


async def _seed_invoice(tenant_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="PG Invoice Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number=f"INV-PG-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=InvoiceStatus.OVERDUE, issue_date=date.today() - timedelta(days=40),
            due_date=date.today() - timedelta(days=20), currency="USD", total=750, amount_due=750,
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        return invoice.id


@requires_real_postgres
async def test_concurrent_duplicate_invoice_overdue_events_produce_at_most_one_notification(monkeypatch) -> None:
    fake_provider = _FakeProvider()
    monkeypatch.setattr("app.services.ai_provider.get_ai_provider", lambda: fake_provider)

    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport
    from app.models.automation import TriggerType

    tenant_id = uuid.uuid4()
    invoice_id = await _seed_invoice(tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    service = AutomationService(async_session_maker, AIExecutionService(registry))

    automation = await service.create_automation(
        tenant_id, name="AI Decides Invoice Followup (PG concurrency)", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": "exception.created"},
        condition={"field": "invoice.type", "op": "eq", "value": "INVOICE_OVERDUE"},
        steps=[{"action": "ai.propose_invoice_followup", "params": {"invoice_id": "{{event.entity_id}}"}}],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    event_id = uuid.uuid4()

    async def fire():
        return await service.start_execution(
            tenant_id, published, version, trigger_type=TriggerType.EVENT, source_event_id=event_id,
            entity_type="invoice", entity_id=invoice_id, context={"event": {"entity_id": str(invoice_id)}, "invoice": {"type": "INVOICE_OVERDUE"}},
            triggered_by=None,
        )

    results = await asyncio.gather(*[fire() for _ in range(10)])
    successes = [r for r in results if r is not None]
    assert len(successes) == 1, f"expected exactly one non-deduplicated execution, got {len(successes)}"

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
        invocations = (
            await session.execute(
                select(AIInvocationLog).where(
                    AIInvocationLog.tenant_id == tenant_id, AIInvocationLog.operation == "ai_next_action_invoice_followup",
                )
            )
        ).scalars().all()

    assert len(notifications) == 1
    assert len(invocations) == 1
    assert fake_provider.call_count == 1


@requires_real_postgres
async def test_invoice_decision_tenant_isolation_on_real_postgres(monkeypatch) -> None:
    fake_provider = _FakeProvider()
    monkeypatch.setattr("app.services.ai_provider.get_ai_provider", lambda: fake_provider)

    from app.ai.execution_service import ToolRequest  # noqa: F401 — sanity import, unused directly
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport
    from app.services.ai_next_action_service import AINextActionService
    from app.services.company_memory_service import CompanyMemoryService

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    invoice_a = await _seed_invoice(tenant_a)

    memory = CompanyMemoryService(async_session_maker)
    await memory.create_memory(
        tenant_a, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tenant_a_pg_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )
    await memory.create_memory(
        tenant_b, memory_type="OWNER_PREFERENCE", key="brand_tone", value="tenant_b_pg_marker",
        description=None, source="OWNER_EXPLICIT", created_by=None,
    )

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)

    class _CapturingProvider(_FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.last_prompt = None

        async def generate_structured(self, prompt: str) -> AICallOutcome:
            self.last_prompt = prompt
            return await super().generate_structured(prompt)

    capturing = _CapturingProvider()
    service = AINextActionService(async_session_maker, AIExecutionService(registry), capturing)
    await service.decide_invoice_followup(tenant_a, invoice_a, correlation_id=uuid.uuid4())

    assert "tenant_a_pg_marker" in capturing.last_prompt
    assert "tenant_b_pg_marker" not in capturing.last_prompt
