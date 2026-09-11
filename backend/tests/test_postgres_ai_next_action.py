"""Phase 18: real PostgreSQL concurrency verification for the AI Next
Action decision layer. SQLite serializes writers to the same file, so it
can never prove a genuine race the way real PostgreSQL can (see
tests/test_postgres_automation_concurrency.py's own header for the
precedent this file follows).

This does not re-derive idempotency from scratch — the guarantee is the
SAME real `AutomationExecution` unique constraint on
(automation_version_id, source_event_id) proven generically in
test_postgres_automation_concurrency.py. What this file proves
specifically is that the AI decision layer inherits it end to end: N
truly concurrent duplicate `quote.expired` deliveries must produce at most
one real AI call, at most one real Notification, and at most one
AIInvocationLog row — never a duplicate business mutation just because
the decision layer sits in the middle.
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
from app.models.notification import Notification
from app.models.quote import Quote, QuoteStatus
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
    "reason": "Quote expired with no response.",
    "confidence": 0.8,
    "arguments": {"title": "Quote expired", "body": "A quote went stale."},
}


class _FakeProvider:
    is_connected = True
    name = "fake"
    model = "fake-model-1"

    def __init__(self) -> None:
        self.call_count = 0

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.call_count += 1
        return AICallOutcome(
            success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=json.dumps(_VALID_PROPOSAL)
        )


async def _seed_quote(tenant_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="PG Concurrency Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number="Q-PG-0001", customer_id=customer.id,
            status=QuoteStatus.EXPIRED, currency="USD", subtotal=500, tax=0, discount=0, total=500,
            valid_until=date.today() - timedelta(days=1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)
        return quote.id


@requires_real_postgres
async def test_concurrent_duplicate_quote_expired_events_produce_at_most_one_notification(monkeypatch) -> None:
    fake_provider = _FakeProvider()
    monkeypatch.setattr("app.services.ai_provider.get_ai_provider", lambda: fake_provider)

    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    tenant_id = uuid.uuid4()
    quote_id = await _seed_quote(tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    service = AutomationService(async_session_maker, AIExecutionService(registry))

    from app.models.automation import TriggerType

    automation = await service.create_automation(
        tenant_id, name="AI Decides Quote Followup (PG concurrency)", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": "quote.expired"}, condition=None,
        steps=[{"action": "ai.propose_quote_followup", "params": {"quote_id": "{{event.entity_id}}"}}],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    event_id = uuid.uuid4()

    async def fire():
        return await service.start_execution(
            tenant_id, published, version, trigger_type=TriggerType.EVENT, source_event_id=event_id,
            entity_type="quote", entity_id=quote_id, context={"event": {"entity_id": str(quote_id)}}, triggered_by=None,
        )

    results = await asyncio.gather(*[fire() for _ in range(10)])
    successes = [r for r in results if r is not None]
    assert len(successes) == 1, f"expected exactly one non-deduplicated execution, got {len(successes)}"

    # run_steps for non-waiting automations is invoked synchronously inside
    # start_execution — give any remaining async work a moment, then verify
    # the real downstream effects, not just the AutomationExecution count.
    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
        invocations = (
            await session.execute(
                select(AIInvocationLog).where(
                    AIInvocationLog.tenant_id == tenant_id,
                    AIInvocationLog.operation == "ai_next_action_quote_followup",
                )
            )
        ).scalars().all()

    assert len(notifications) == 1
    assert len(invocations) == 1
    assert fake_provider.call_count == 1


@requires_real_postgres
async def test_concurrent_tenant_tool_policy_writes_stay_consistent_for_ai_action() -> None:
    """The AI decision layer's own governed entry point resolves policy
    through the same PolicyService/TenantToolPolicy machinery as every
    other tool — this proves that machinery's real unique constraint
    (tenant_id, tool_name) holds for THIS tool name specifically, under
    genuine concurrent writers."""
    from app.models.tool_policy import TenantToolPolicy
    from app.services.policy_service import ActionPolicy
    from app.tools.errors import ToolBlockedError

    tenant_id = uuid.uuid4()

    async def write(policy: str):
        async with async_session_maker() as session:
            existing = (
                await session.execute(
                    select(TenantToolPolicy).where(
                        TenantToolPolicy.tenant_id == tenant_id,
                        TenantToolPolicy.tool_name == "ai.propose_quote_followup",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None
            session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="ai.propose_quote_followup", policy=policy, enabled=True))
            try:
                await session.commit()
                return policy
            except Exception:
                await session.rollback()
                return None

    results = await asyncio.gather(*[write(ActionPolicy.BLOCKED) for _ in range(8)])
    winners = [r for r in results if r is not None]
    assert len(winners) == 1

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(TenantToolPolicy).where(
                    TenantToolPolicy.tenant_id == tenant_id, TenantToolPolicy.tool_name == "ai.propose_quote_followup",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
