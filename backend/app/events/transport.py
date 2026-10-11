"""Transport abstraction for the event bus.

Business logic (idempotency, retries, dead-lettering — see app/events/bus.py)
depends only on this interface, never on Redis directly. Swapping to
Kafka/NATS later means writing a new EventTransport implementation, not
touching the bus or any handler.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TransportMessage:
    message_id: str
    fields: dict[str, str]


class EventTransport(ABC):
    # True when a message that was delivered but NOT acked is kept by the transport and can be taken over later (`claim_stale`). Then a delivery that
    # cannot proceed yet (another worker's lease is still running) must be left un-acked so it is retried; acking it would destroy the only copy.
    keeps_unacked_messages: bool = False

    @abstractmethod
    async def ensure_group(self, stream: str, group: str) -> None: ...

    @abstractmethod
    async def send(self, stream: str, fields: dict[str, str]) -> str: ...

    @abstractmethod
    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[TransportMessage]: ...

    @abstractmethod
    async def ack(self, stream: str, group: str, message_id: str) -> None: ...

    async def pending_count(self, stream: str, group: str) -> int | None:
        """Best-effort queue depth for a stream/group — how many messages are
        published but not yet acked by this consumer group. Returns None if
        the transport can't answer cheaply (base default; overridden below).
        """
        return None

    async def claim_stale(
        self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int = 10
    ) -> list[TransportMessage]:
        """Messages that were DELIVERED to some consumer but never acked and have been idle for at least `min_idle_ms` (the consumer that read
        them crashed). Redis Streams never redelivers these through a plain read (`>` only returns never-delivered messages), so a transport that
        has such a state must implement this. Default: none (a transport whose unacked messages are simply read again needs nothing)."""
        return []

    async def set_heartbeat(self, name: str, ttl_seconds: int) -> None:
        """Worker liveness marker, best effort. Default: nothing."""

    async def heartbeat_age_seconds(self, name: str) -> float | None:
        """Seconds since `set_heartbeat(name)` last ran, or None if never / expired / unsupported."""
        return None


class RedisStreamTransport(EventTransport):
    """Real transport, backed by Redis Streams + consumer groups.

    Verified directly against a live Redis server (Phase 12B, see
    tests/test_redis_stream_transport.py) — XGROUP CREATE / XREADGROUP /
    XACK / XPENDING all exercised for real. Note: the EventBus/worker test
    suite always runs against InMemoryTransport regardless of
    EVENT_TRANSPORT (see factory.py's docstring) — that suite verifies
    EventBus's dedup/retry/dead-letter logic, which lives in Postgres and
    is transport-agnostic, not this class's Redis wiring specifically.
    """

    keeps_unacked_messages = True

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP if it already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def send(self, stream: str, fields: dict[str, str]) -> str:
        message_id = await self._redis.xadd(stream, fields)
        return message_id if isinstance(message_id, str) else message_id.decode()

    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[TransportMessage]:
        result = await self._redis.xreadgroup(group, consumer, {stream: ">"}, count=count)
        messages: list[TransportMessage] = []
        for _stream_name, entries in result or []:
            for message_id, fields in entries:
                mid = message_id if isinstance(message_id, str) else message_id.decode()
                decoded = {
                    (k if isinstance(k, str) else k.decode()): (v if isinstance(v, str) else v.decode())
                    for k, v in fields.items()
                }
                messages.append(TransportMessage(message_id=mid, fields=decoded))
        return messages

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        await self._redis.xack(stream, group, message_id)

    async def claim_stale(
        self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int = 10
    ) -> list[TransportMessage]:
        """XAUTOCLAIM: take over messages another (crashed) consumer read but never acked. Needs Redis >= 6.2."""
        try:
            result = await self._redis.xautoclaim(stream, group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count)
        except Exception as exc:  # noqa: BLE001 - no such stream/group yet is normal; anything else is surfaced by the caller's next read
            if "NOGROUP" in str(exc) or "no such key" in str(exc).lower():
                return []
            raise
        entries = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
        messages: list[TransportMessage] = []
        for message_id, fields in entries or []:
            if not fields:  # an entry deleted from the stream but still in the pending list
                await self._redis.xack(stream, group, message_id if isinstance(message_id, str) else message_id.decode())
                continue
            mid = message_id if isinstance(message_id, str) else message_id.decode()
            messages.append(TransportMessage(message_id=mid, fields={(k if isinstance(k, str) else k.decode()): (v if isinstance(v, str) else v.decode()) for k, v in fields.items()}))
        return messages

    async def set_heartbeat(self, name: str, ttl_seconds: int) -> None:
        import time

        await self._redis.set(name, repr(time.time()), ex=ttl_seconds)

    async def heartbeat_age_seconds(self, name: str) -> float | None:
        import time

        raw = await self._redis.get(name)
        if raw is None:
            return None
        try:
            return max(0.0, time.time() - float(raw if isinstance(raw, str) else raw.decode()))
        except ValueError:
            return None

    async def pending_count(self, stream: str, group: str) -> int | None:
        try:
            summary = await self._redis.xpending(stream, group)
        except Exception:  # noqa: BLE001 — group/stream may not exist yet; not a real error
            return 0
        if not summary:
            return 0
        return int(summary.get("pending", 0)) if isinstance(summary, dict) else int(summary[0])


class InMemoryTransport(EventTransport):
    """In-process transport used in tests and as a documented dev fallback
    when no Redis is configured. Implements the same delivery semantics
    (per-consumer-group cursors, at-least-once delivery) as the Redis
    implementation so bus logic is exercised faithfully.
    """

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._group_offsets: dict[tuple[str, str], int] = {}
        self._counters: dict[str, int] = {}

    async def ensure_group(self, stream: str, group: str) -> None:
        self._streams.setdefault(stream, [])
        self._group_offsets.setdefault((stream, group), 0)

    async def send(self, stream: str, fields: dict[str, str]) -> str:
        self._counters[stream] = self._counters.get(stream, 0) + 1
        message_id = f"{stream}-{self._counters[stream]}"
        self._streams.setdefault(stream, []).append((message_id, dict(fields)))
        return message_id

    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[TransportMessage]:
        del consumer
        offset = self._group_offsets.get((stream, group), 0)
        entries = self._streams.get(stream, [])[offset : offset + count]
        return [TransportMessage(message_id=mid, fields=fields) for mid, fields in entries]

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        entries = self._streams.get(stream, [])
        offset = self._group_offsets.get((stream, group), 0)
        for i, (mid, _fields) in enumerate(entries[offset:], start=offset):
            if mid == message_id:
                self._group_offsets[(stream, group)] = i + 1
                return

    async def pending_count(self, stream: str, group: str) -> int | None:
        offset = self._group_offsets.get((stream, group), 0)
        return max(len(self._streams.get(stream, [])) - offset, 0)
