"""Phase 6 (Agent Runtime Reliability): MANDATORY real-Postgres-only
concurrency proofs for scheduled/event trigger idempotency — two
overlapping ticks / two redeliveries racing to create the SAME logical
`AgentExecution` must produce exactly one row, enforced by the real DB
unique constraint `uq_agent_executions_tenant_agent_idempotency`
(tenant_id, agent_id, idempotency_key), not app-level de-duplication
alone. Skipped entirely unless DATABASE_URL points at a real PostgreSQL
instance.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.agent_trigger_handlers import register_agent_trigger_handlers
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.agent import AgentAutonomyTier, AgentExecution, AgentStatus
from app.models.event import EventType
from app.models.organization import Organization
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_service import AgentService
from app.services.agent_trigger_service import AgentTriggerService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        text = json.dumps({"action": "COMPLETE", "final_response": "done", "reasoning_summary": "r", "arguments": {}})
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


async def _execution_count(tenant_id) -> int:
    from sqlalchemy import func, select

    async with async_session_maker() as session:
        return (
            await session.execute(select(func.count(AgentExecution.id)).where(AgentExecution.tenant_id == tenant_id))
        ).scalar_one()


@requires_real_postgres
async def test_duplicate_scheduled_dispatch_race_creates_exactly_one_execution(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name="PGScheduleRace", slug=f"pgs-{tenant_id.hex[:8]}"))
        await session.commit()

    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="PGScheduleRaceAgent", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x",
        triggers={"schedule": {"enabled": True, "frequency": "DAILY", "time": "09:00", "goal": "daily"}},
        created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    def _svc():
        exec_service = AgentExecutionService(async_session_maker, tool_registry)
        rs = AgentReasoningService(async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider())
        return AgentTriggerService(async_session_maker, rs)

    now = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
    # Two overlapping ticks, same due occurrence, genuinely concurrent
    # asyncpg transactions racing on the SAME real DB unique constraint.
    await asyncio.gather(
        _svc().check_and_dispatch_scheduled(tenant_id, now_utc=now),
        _svc().check_and_dispatch_scheduled(tenant_id, now_utc=now),
    )
    assert await _execution_count(tenant_id) == 1


@requires_real_postgres
async def test_duplicate_event_delivery_race_creates_exactly_one_execution(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="PGEventRaceAgent", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x",
        triggers={"event": {"enabled": True, "event_type": EventType.LEAD_CREATED.value, "goal": "g"}},
        created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_agent_trigger_handlers(bus, async_session_maker, tool_registry, FakeAIProvider())
    event = await bus.publish(tenant_id=tenant_id, event_type=EventType.LEAD_CREATED, source="test", payload={})

    subs = bus._subscriptions[EventType.LEAD_CREATED.value]
    handler = next(s.handler for s in subs if s.handler_name == "agent_trigger_dispatch")

    # Two genuinely concurrent "redeliveries" of the SAME event racing on
    # the same real DB unique idempotency constraint.
    await asyncio.gather(handler(event), handler(event))
    assert await _execution_count(tenant_id) == 1


@requires_real_postgres
async def test_concurrent_duplicate_idempotency_key_never_raises_raw_integrity_error(tool_registry) -> None:
    """Direct proof of the real bug found and fixed in this phase (see
    PHASE_6_IMPLEMENTATION_LOG.md §13 and AgentReasoningService.start's/
    AgentExecutionService._create_execution_row's matching fixes): five
    genuinely concurrent `AgentReasoningService.start()` calls with the
    SAME idempotency key must resolve to exactly one successful execution
    and four `DuplicateExecutionRequestError`s — never a raw, unhandled
    `IntegrityError` escaping past the check-then-insert race window.
    Concurrency/rate ceilings are set high so they cannot mask the race."""
    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="PGIdempotencyRace", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x", created_by=None,
        max_concurrent_executions=100, max_executions_per_hour=1000,
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    exec_service = AgentExecutionService(async_session_maker, tool_registry)

    def _rs():
        return AgentReasoningService(async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider())

    key = "race-key-postgres"

    from app.services.agent_execution_service import DuplicateExecutionRequestError

    async def attempt():
        try:
            return ("OK", await _rs().start(tenant_id, agent.id, goal="g", triggered_by=None, idempotency_key=key))
        except DuplicateExecutionRequestError as exc:
            return ("DUP", exc.existing_execution_id)

    results = await asyncio.gather(*(attempt() for _ in range(5)))
    kinds = [r[0] for r in results]
    assert kinds.count("OK") == 1
    assert kinds.count("DUP") == 4
    assert await _execution_count(tenant_id) == 1
