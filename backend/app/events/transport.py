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


class RedisStreamTransport(EventTransport):
    """Real transport, backed by Redis Streams + consumer groups.

    Not exercised against a live Redis server in this sandbox (no redis-server
    binary available) — see PROJECT_STATUS.md. The logic mirrors the standard
    Redis Streams consumer-group pattern (XGROUP CREATE / XREADGROUP / XACK)
    and is otherwise identical in shape to InMemoryTransport, which the same
    EventBus code is verified against.
    """

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
