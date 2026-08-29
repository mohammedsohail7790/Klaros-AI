"""Phase 12B: RedisStreamTransport verified directly against a real Redis
server. The rest of the test suite's `event_bus` fixture always uses
InMemoryTransport regardless of EVENT_TRANSPORT (see
app/events/factory.py's docstring) — that suite verifies EventBus's
dedup/retry/dead-letter logic (Postgres-backed, transport-agnostic), not
this class's actual XADD/XREADGROUP/XACK/XPENDING wiring. This file is
skipped entirely unless REDIS_URL points at a real, reachable Redis."""

import uuid

import pytest

from app.core.config import get_settings
from app.events.bus import EventBus
from app.events.transport import RedisStreamTransport

pytestmark = pytest.mark.asyncio

_settings = get_settings()


def _redis_available() -> bool:
    if _settings.EVENT_TRANSPORT == "memory":
        return False
    try:
        import redis as sync_redis

        client = sync_redis.from_url(_settings.REDIS_URL, socket_connect_timeout=2)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


requires_real_redis = pytest.mark.skipif(
    not _redis_available(), reason="requires REDIS_URL pointed at a real, reachable Redis"
)


@pytest.fixture
async def redis_client():
    import redis.asyncio as redis

    client = redis.from_url(_settings.REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


@requires_real_redis
async def test_send_and_read_group_round_trip_through_real_redis(redis_client) -> None:
    transport = RedisStreamTransport(redis_client)
    stream = f"klaros.test.{uuid.uuid4()}"
    group = "test-group"

    await transport.ensure_group(stream, group)
    message_id = await transport.send(stream, {"event_id": "abc123", "event_type": "test.event"})
    assert message_id

    messages = await transport.read_group(stream, group, consumer="c1")
    assert len(messages) == 1
    assert messages[0].fields["event_id"] == "abc123"

    # Unacked message must still be pending.
    assert await transport.pending_count(stream, group) == 1

    await transport.ack(stream, group, messages[0].message_id)
    assert await transport.pending_count(stream, group) == 0


@requires_real_redis
async def test_ensure_group_is_idempotent_against_a_real_stream(redis_client) -> None:
    transport = RedisStreamTransport(redis_client)
    stream = f"klaros.test.{uuid.uuid4()}"
    group = "test-group"

    await transport.ensure_group(stream, group)
    await transport.ensure_group(stream, group)  # must not raise BUSYGROUP outward


@requires_real_redis
async def test_two_consumers_in_the_same_group_split_real_redis_messages(redis_client) -> None:
    """Proves consumer-group semantics work for real: each message is
    delivered to exactly one consumer in the group, not both."""
    transport = RedisStreamTransport(redis_client)
    stream = f"klaros.test.{uuid.uuid4()}"
    group = "test-group"
    await transport.ensure_group(stream, group)

    for i in range(4):
        await transport.send(stream, {"seq": str(i)})

    c1_messages = await transport.read_group(stream, group, consumer="c1", count=2)
    c2_messages = await transport.read_group(stream, group, consumer="c2", count=2)

    assert len(c1_messages) == 2
    assert len(c2_messages) == 2
    seqs = {m.fields["seq"] for m in c1_messages} | {m.fields["seq"] for m in c2_messages}
    assert seqs == {"0", "1", "2", "3"}


@requires_real_redis
async def test_full_event_bus_pipeline_through_real_redis_transport(redis_client, monkeypatch) -> None:
    """End-to-end: EventBus.publish -> real Redis XADD -> real XREADGROUP
    via process_pending -> handler runs -> real XACK. Uses a disposable
    stream prefix (random suffix) so it never collides with the app's real
    consumer group state."""
    from app.db.session import async_session_maker

    stream_prefix = f"klaros.test.{uuid.uuid4()}"
    transport = RedisStreamTransport(redis_client)
    bus = EventBus(session_factory=async_session_maker, transport=transport, stream_prefix=stream_prefix)

    received: list[str] = []

    async def handler(event) -> None:
        received.append(event.event_type)

    bus.subscribe("verification.pipeline_test", "test_handler", handler)

    tenant_id = uuid.uuid4()
    event = await bus.publish(
        tenant_id=tenant_id, event_type="verification.pipeline_test", source="test", payload={"k": "v"}
    )
    assert event is not None

    stats = await bus.process_pending("verification.pipeline_test")
    assert stats.succeeded == 1
    assert received == ["verification.pipeline_test"]

    # Duplicate delivery: re-publish with the SAME idempotency key must
    # dedupe at the Postgres layer, never reaching the transport twice.
    dup_event = await bus.publish(
        tenant_id=tenant_id,
        event_type="verification.pipeline_test",
        source="test",
        payload={"k": "v"},
        idempotency_key="dup-key-1",
    )
    dup_event_2 = await bus.publish(
        tenant_id=tenant_id,
        event_type="verification.pipeline_test",
        source="test",
        payload={"k": "v"},
        idempotency_key="dup-key-1",
    )
    assert dup_event.id == dup_event_2.id
    assert dup_event_2.was_deduplicated is True
