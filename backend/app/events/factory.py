"""Process-wide EventBus singleton, wired to Redis in normal operation.

Tests construct their own EventBus with an InMemoryTransport instead of
calling into this module — see backend/tests/conftest.py.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport, RedisStreamTransport


@lru_cache
def get_event_bus() -> EventBus:
    settings = get_settings()

    if settings.EVENT_TRANSPORT == "memory":
        transport = InMemoryTransport()
    else:
        import redis.asyncio as redis

        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        transport = RedisStreamTransport(redis_client)

    return EventBus(
        session_factory=async_session_maker,
        transport=transport,
        processing_lease_seconds=settings.EVENT_PROCESSING_LEASE_SECONDS,
        pending_reclaim_idle_ms=settings.EVENT_PENDING_RECLAIM_IDLE_MS,
        consumer_name=settings.EVENT_WORKER_CONSUMER_NAME,
    )
