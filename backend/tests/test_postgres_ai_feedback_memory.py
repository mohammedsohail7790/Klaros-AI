"""Phase 19: real PostgreSQL concurrency verification that repeated/
concurrent approval decisions cannot create duplicate AI_FEEDBACK Company
Memory. No new migration/constraint was added for this — the guarantee is
entirely inherited from the EXISTING `ApprovalRequest.status` real
database-level compare-and-swap (see ApprovalExecutionService.approve()/
reject()), proven generically for approval state itself in
tests/test_approval_flow.py; this file proves the SAME guarantee reaches
the new Phase 19 learning hook specifically, under a genuine race against
real PostgreSQL (SQLite serializes writers to the same file and can never
prove this the way real PostgreSQL can)."""

import asyncio
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.company_memory import CompanyMemory, MemoryStatus, MemoryType
from app.models.crm import Customer, CustomerStatus
from app.models.quote import Quote, QuoteStatus
from app.models.rbac import Role
from app.models.tool_policy import TenantToolPolicy
from app.services.ai_next_action_service import AINextActionService
from app.services.ai_provider import AICallOutcome
from app.services.approval_execution_service import ApprovalExecutionService, ApprovalStateError
from app.services.policy_service import ActionPolicy
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

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=5, raw_text=json.dumps(_VALID_PROPOSAL))


async def _seed_quote(tenant_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="PG Feedback Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"Q-PGFB-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=QuoteStatus.EXPIRED, currency="USD", subtotal=500, tax=0, discount=0, total=500,
            valid_until=date.today() - timedelta(days=1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)
        return quote.id


async def _feedback_memories(tenant_id: uuid.UUID) -> list[CompanyMemory]:
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(CompanyMemory).where(CompanyMemory.tenant_id == tenant_id, CompanyMemory.memory_type == MemoryType.AI_FEEDBACK)
            )
        ).scalars().all()
    return list(rows)


@requires_real_postgres
async def test_concurrent_approve_calls_produce_exactly_one_feedback_memory() -> None:
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
        await session.commit()
    quote_id = await _seed_quote(tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    decision_service = AINextActionService(async_session_maker, AIExecutionService(registry), _FakeProvider())
    decision = await decision_service.decide_quote_followup(tenant_id, quote_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "approval_required"
    approval_id = decision.approval_request_id

    approvals = ApprovalExecutionService(async_session_maker, registry, bus)
    owner_id = uuid.uuid4()

    async def approve_once():
        try:
            return await approvals.approve(tenant_id, approval_id, decided_by_id=owner_id, decided_by_role=Role.OWNER)
        except ApprovalStateError:
            return None

    results = await asyncio.gather(*[approve_once() for _ in range(10)])
    successes = [r for r in results if r is not None]
    assert len(successes) == 1, f"expected exactly one non-deduplicated approval, got {len(successes)}"

    memories = await _feedback_memories(tenant_id)
    assert len(memories) == 1, f"expected exactly one AI_FEEDBACK memory from 10 concurrent approve() calls, got {len(memories)}"
    assert memories[0].status == MemoryStatus.PENDING


@requires_real_postgres
async def test_concurrent_reject_calls_produce_exactly_one_feedback_memory() -> None:
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(TenantToolPolicy(tenant_id=tenant_id, tool_name="notifications.create_notification", policy=ActionPolicy.APPROVAL_REQUIRED, enabled=True))
        await session.commit()
    quote_id = await _seed_quote(tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    decision_service = AINextActionService(async_session_maker, AIExecutionService(registry), _FakeProvider())
    decision = await decision_service.decide_quote_followup(tenant_id, quote_id, correlation_id=uuid.uuid4())
    assert decision.outcome == "approval_required"
    approval_id = decision.approval_request_id

    approvals = ApprovalExecutionService(async_session_maker, registry, bus)
    owner_id = uuid.uuid4()

    async def reject_once():
        try:
            return await approvals.reject(tenant_id, approval_id, decided_by_id=owner_id)
        except ApprovalStateError:
            return None

    results = await asyncio.gather(*[reject_once() for _ in range(10)])
    successes = [r for r in results if r is not None]
    assert len(successes) == 1

    memories = await _feedback_memories(tenant_id)
    assert len(memories) == 1
