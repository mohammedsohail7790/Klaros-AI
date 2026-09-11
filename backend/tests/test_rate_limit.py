"""Rate limiting (app/core/rate_limit.py) — the highest-priority
credential-free gap identified in ARCHITECTURE_TRACEABILITY.md. Covers the
backend directly (unit) and its wiring into public endpoints (integration).
"""

import uuid

import pytest

from app.core.config import get_settings
from app.core.rate_limit import InMemoryRateLimitBackend, reset_rate_limit_backend

pytestmark = pytest.mark.asyncio


# --- Backend unit tests ---------------------------------------------------

async def test_below_limit_is_allowed() -> None:
    backend = InMemoryRateLimitBackend()
    for _ in range(5):
        result = await backend.hit("k", limit=10, window_seconds=60)
        assert result.allowed is True


async def test_exactly_at_limit_is_allowed() -> None:
    backend = InMemoryRateLimitBackend()
    result = None
    for _ in range(10):
        result = await backend.hit("k", limit=10, window_seconds=60)
    assert result.allowed is True
    assert result.remaining == 0


async def test_above_limit_is_rejected() -> None:
    backend = InMemoryRateLimitBackend()
    for _ in range(10):
        await backend.hit("k", limit=10, window_seconds=60)
    result = await backend.hit("k", limit=10, window_seconds=60)
    assert result.allowed is False
    assert result.retry_after_seconds >= 1


async def test_different_keys_have_independent_counters() -> None:
    backend = InMemoryRateLimitBackend()
    for _ in range(10):
        await backend.hit("tenant-a", limit=10, window_seconds=60)
    result_a = await backend.hit("tenant-a", limit=10, window_seconds=60)
    result_b = await backend.hit("tenant-b", limit=10, window_seconds=60)
    assert result_a.allowed is False
    assert result_b.allowed is True


async def test_window_resets_after_expiry(monkeypatch) -> None:
    import app.core.rate_limit as rl

    now = [1_000_000]
    monkeypatch.setattr(rl.time, "time", lambda: now[0])
    backend = InMemoryRateLimitBackend()
    for _ in range(10):
        await backend.hit("k", limit=10, window_seconds=60)
    assert (await backend.hit("k", limit=10, window_seconds=60)).allowed is False

    now[0] += 61
    assert (await backend.hit("k", limit=10, window_seconds=60)).allowed is True


# --- Integration: public endpoints ----------------------------------------

@pytest.fixture(autouse=True)
def _tight_public_lead_limit(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE", 3)
    reset_rate_limit_backend()
    yield
    reset_rate_limit_backend()


async def test_public_lead_endpoint_returns_429_over_limit(client) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Rate Limit Test Co", "full_name": "Owner",
            "email": "ratelimit-owner@example.com", "password": "supersecret1",
        },
    )
    tenant_id = resp.json()["user"]["tenant_id"]

    for i in range(3):
        resp = await client.post(
            f"/api/v1/public/leads/{tenant_id}",
            json={"name": f"Lead {i}", "source": "WEB", "email": f"lead{i}@example.com"},
        )
        assert resp.status_code == 201, resp.text

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={"name": "One too many", "source": "WEB", "email": "toomany@example.com"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


async def test_public_lead_rate_limit_is_per_tenant(client) -> None:
    resp_a = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Rate Limit A", "full_name": "Owner",
            "email": "ratelimit-a@example.com", "password": "supersecret1",
        },
    )
    resp_b = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Rate Limit B", "full_name": "Owner",
            "email": "ratelimit-b@example.com", "password": "supersecret1",
        },
    )
    tenant_a = resp_a.json()["user"]["tenant_id"]
    tenant_b = resp_b.json()["user"]["tenant_id"]

    for i in range(3):
        resp = await client.post(
            f"/api/v1/public/leads/{tenant_a}",
            json={"name": f"Lead {i}", "source": "WEB", "email": f"a-lead{i}@example.com"},
        )
        assert resp.status_code == 201

    # Tenant A is now at its limit — tenant B, same caller IP, is unaffected.
    resp = await client.post(
        f"/api/v1/public/leads/{tenant_b}",
        json={"name": "Tenant B Lead", "source": "WEB", "email": "b-lead@example.com"},
    )
    assert resp.status_code == 201


async def test_public_lead_endpoint_ignores_limit_when_disabled(client, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Rate Limit Disabled Co", "full_name": "Owner",
            "email": "ratelimit-disabled@example.com", "password": "supersecret1",
        },
    )
    tenant_id = resp.json()["user"]["tenant_id"]

    for i in range(5):
        resp = await client.post(
            f"/api/v1/public/leads/{tenant_id}",
            json={"name": f"Lead {i}", "source": "WEB", "email": f"disabled-lead{i}@example.com"},
        )
        assert resp.status_code == 201, resp.text


async def test_malformed_body_still_counts_toward_the_limit(client) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Rate Limit Malformed Co", "full_name": "Owner",
            "email": "ratelimit-malformed@example.com", "password": "supersecret1",
        },
    )
    tenant_id = resp.json()["user"]["tenant_id"]

    for _ in range(3):
        resp = await client.post(f"/api/v1/public/leads/{tenant_id}", json={"source": "WEB"})
        assert resp.status_code == 422

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={"name": "Real Lead", "source": "WEB", "email": "real@example.com"},
    )
    assert resp.status_code == 429


async def test_redis_unavailable_raises_rather_than_silently_falling_back(monkeypatch) -> None:
    """RATE_LIMIT_BACKEND=redis must never silently degrade to an
    in-process limit it was never configured for — an unreachable Redis
    should surface as a real connection error the deployment can alert on."""
    from app.core import rate_limit as rl

    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:1/0?socket_connect_timeout=1")
    reset_rate_limit_backend()

    backend = rl.get_rate_limit_backend()
    assert isinstance(backend, rl.RedisRateLimitBackend)
    with pytest.raises(Exception):
        await backend.hit(f"probe:{uuid.uuid4()}", limit=1, window_seconds=60)
    reset_rate_limit_backend()
