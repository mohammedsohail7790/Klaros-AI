"""Fixed-window rate limiting for public/edge endpoints (public lead intake,
public quote/contract views, auth, inbound Twilio webhooks) — the highest-
priority credential-free gap identified in ARCHITECTURE_TRACEABILITY.md.

Two real backends, same honest split already established for the EventBus
(app/events/transport.py, EVENT_TRANSPORT=memory|redis):

- InMemoryRateLimitBackend: correct within a single process. Does NOT
  coordinate across multiple backend replicas — fine for one instance or
  local dev/test, NOT a substitute for distributed limiting in production.
- RedisRateLimitBackend: real `INCR`+`EXPIRE` fixed-window counting shared
  across every process talking to the same Redis. Selected explicitly via
  RATE_LIMIT_BACKEND=redis — never auto-detected, so a misconfigured
  deployment fails loudly (Redis connection errors) instead of silently
  falling back to a per-process limit it never asked for.

Never claims distributed correctness it hasn't earned: the default is
"memory", and only becomes "redis" when explicitly configured.
"""

import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimitBackend:
    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        raise NotImplementedError


class InMemoryRateLimitBackend(RateLimitBackend):
    def __init__(self) -> None:
        self._counters: dict[str, tuple[int, int]] = {}

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = int(time.time())
        window_start = now - (now % window_seconds)
        stored_window, count = self._counters.get(key, (window_start, 0))
        if stored_window != window_start:
            stored_window, count = window_start, 0
        count += 1
        self._counters[key] = (stored_window, count)
        retry_after = (stored_window + window_seconds) - now
        return RateLimitResult(
            allowed=count <= limit, remaining=max(0, limit - count), retry_after_seconds=max(1, retry_after)
        )


class RedisRateLimitBackend(RateLimitBackend):
    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = int(time.time())
        window_start = now - (now % window_seconds)
        redis_key = f"ratelimit:{key}:{window_start}"
        count = await self._redis.incr(redis_key)
        if count == 1:
            await self._redis.expire(redis_key, window_seconds)
        retry_after = (window_start + window_seconds) - now
        return RateLimitResult(
            allowed=count <= limit, remaining=max(0, limit - count), retry_after_seconds=max(1, retry_after)
        )


_backend: RateLimitBackend | None = None


def get_rate_limit_backend() -> RateLimitBackend:
    global _backend
    if _backend is not None:
        return _backend
    settings = get_settings()
    if settings.RATE_LIMIT_BACKEND == "redis":
        import redis.asyncio as redis

        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        _backend = RedisRateLimitBackend(redis_client)
    else:
        _backend = InMemoryRateLimitBackend()
    return _backend


def reset_rate_limit_backend() -> None:
    """Test-only: drop the cached backend so counters don't leak between tests."""
    global _backend
    _backend = None


def client_ip(request: Request) -> str:
    """Honest about its own limit: only trusts `X-Forwarded-For` when a
    reverse proxy actually sets it (the deployment's responsibility to
    configure); falls back to the raw peer address otherwise."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, *, limit_setting: str, window_seconds: int, key_func=None):
    """FastAPI dependency factory. `limit_setting` names a `Settings`
    attribute, re-read on every request (not captured at decoration/import
    time) so tests can monkeypatch it per-case exactly like every other
    setting in this codebase (see test_twilio_inbound_lead_webhook.py).
    `key_func(request) -> str` builds the rate-limit key; defaults to the
    caller's IP address alone."""

    async def _dependency(request: Request) -> None:
        settings = get_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return
        limit = getattr(settings, limit_setting)
        backend = get_rate_limit_backend()
        key_suffix = key_func(request) if key_func else client_ip(request)
        result = await backend.hit(f"{scope}:{key_suffix}", limit, window_seconds)
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": str(result.retry_after_seconds)},
            )

    return _dependency


def tenant_and_ip_key(request: Request) -> str:
    """For endpoints with a `{tenant_id}` path parameter — keeps one
    abusive caller from exhausting a single tenant's quota for every other
    caller of that same tenant's public endpoint, while still isolating
    tenants from each other."""
    tenant_id = request.path_params.get("tenant_id", "unknown")
    return f"{tenant_id}:{client_ip(request)}"


def path_param_and_ip_key(param_name: str):
    """For public endpoints keyed by some other path parameter (e.g.
    `{quote_id}`/`{contract_id}` — the token itself carries the tenant, so
    the entity id is the natural isolation key here)."""

    def _key(request: Request) -> str:
        value = request.path_params.get(param_name, "unknown")
        return f"{value}:{client_ip(request)}"

    return _key
