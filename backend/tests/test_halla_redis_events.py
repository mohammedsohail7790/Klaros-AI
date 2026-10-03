"""Halla events through the REAL Redis Streams transport (not the in-memory one the rest of the suite uses).

Skipped unless REDIS_URL points at a real Redis (>= 5, streams) and EVENT_TRANSPORT is not "memory". What it proves:
a verified Halla webhook is published on the real stream, delivered to the existing automation dispatcher, runs the
tenant's workflow exactly once, a redelivered webhook never re-publishes, and a consumer that keeps failing is retried a
bounded number of times and then dead-lettered — visibly, without losing the lead update.
"""

import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

from app.core.config import get_settings  # noqa: E402
from app.events.transport import RedisStreamTransport  # noqa: E402
from tests.test_halla_integration import (  # noqa: E402
    BASE,
    _connected,
    _deliver,
    _body,
    _h,
    _lead,
    halla,  # noqa: F401  (fixture)
)

_settings = get_settings()


def _redis_ok() -> bool:
    if _settings.EVENT_TRANSPORT == "memory":
        return False
    try:
        import redis as sync_redis

        c = sync_redis.from_url(_settings.REDIS_URL, socket_connect_timeout=2)
        c.ping()
        major = int(c.info("server")["redis_version"].split(".")[0])
        c.close()
        return major >= 5
    except Exception:
        return False


requires_real_redis = pytest.mark.skipif(not _redis_ok(), reason="requires a real Redis >= 5 via REDIS_URL (and EVENT_TRANSPORT != memory)")


@pytest_asyncio.fixture
async def redis_bus():
    import redis.asyncio as redis

    from app.api.tool_deps import get_wired_event_bus
    from app.ai.execution_service import AIExecutionService
    from app.db.session import async_session_maker
    from app.events.automation_handlers import register_automation_handlers
    from app.events.bus import EventBus
    from app.main import app
    from app.tools.factory import build_tool_registry

    client = redis.from_url(_settings.REDIS_URL, decode_responses=True)
    bus = EventBus(
        session_factory=async_session_maker, transport=RedisStreamTransport(client),
        stream_prefix=f"klaros.test.{uuid.uuid4().hex[:8]}", max_retries=3, retry_backoff_seconds=0.0,
    )
    register_automation_handlers(bus, async_session_maker, AIExecutionService(build_tool_registry(async_session_maker, bus)))
    app.dependency_overrides[get_wired_event_bus] = lambda: bus
    yield bus
    await client.aclose()


@requires_real_redis
async def test_halla_escalation_flows_through_real_redis_to_one_workflow_run(client, halla, redis_bus) -> None:  # noqa: F811
    from app.models.event import EventType

    token, tid = await _connected(client, "Redis Esc", "redisesc@example.com")
    await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))
    lead = await _lead(client, token)
    raw = _body("lead.escalated", eid="evt-redis-1", data={"klaros_lead_id": lead})
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}
    assert (await _deliver(client, tid, raw)).json() == {"status": "duplicate_ignored"}  # redelivery never re-publishes
    stats = await redis_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    assert stats.read == 1 and stats.succeeded == 1
    again = await redis_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    assert again.read == 0  # nothing left to deliver
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    wf = next(w for w in ops["workflows"] if w["name"] == "Escalated lead alert")
    assert wf["runs"] == 1 and wf["last_run"]["status"] == "COMPLETED"


@requires_real_redis
async def test_a_failing_consumer_is_retried_a_bounded_number_of_times_then_dead_lettered(client, halla, redis_bus) -> None:  # noqa: F811
    from sqlalchemy import select

    from app.db.session import async_session_maker, set_tenant_context
    from app.models.event import DeadLetterEvent, Event, EventStatus, EventType

    calls = {"n": 0}

    async def always_fails(event):
        calls["n"] += 1
        raise RuntimeError("downstream is broken")

    redis_bus.subscribe(EventType.HALLA_LEAD_QUALIFIED, "always_fails", always_fails)
    token, tid = await _connected(client, "Redis Dlq", "redisdlq@example.com")
    lead = await _lead(client, token)
    assert (await _deliver(client, tid, _body("lead.qualified", data={"klaros_lead_id": lead, "qualification": "qualified"}))).status_code == 200
    for _ in range(8):
        await redis_bus.process_pending(EventType.HALLA_LEAD_QUALIFIED)
    assert 1 <= calls["n"] <= redis_bus.max_retries + 1  # bounded: never an endless loop
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        ev = (await s.execute(select(Event).where(Event.tenant_id == tid, Event.event_type == EventType.HALLA_LEAD_QUALIFIED.value))).scalar_one()
        dead = (await s.execute(select(DeadLetterEvent).where(DeadLetterEvent.tenant_id == tid))).scalars().all()
    assert ev.status in (EventStatus.DEAD_LETTER, EventStatus.FAILED) and len(dead) >= 1  # failure is visible, not swallowed
    st = (await client.get(f"/api/v1/leads/{lead}", headers=_h(token))).json()
    assert (st.get("lead") or st)["status"] == "QUALIFIED"  # the lead update itself was never lost
