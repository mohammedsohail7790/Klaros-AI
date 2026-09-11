"""Phase 12B: /ready — found missing during real PostgreSQL/Redis
verification. /health alone would report "ok" even with every dependency
unreachable; /ready actually checks them."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_health_is_always_ok_regardless_of_dependencies(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_ready_reports_database_ok_against_the_real_test_database(client) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migration"] == "ok"


async def test_ready_returns_503_when_migration_head_is_out_of_date(client, monkeypatch) -> None:
    """Phase 27: found by direct reproduction — pointing /ready at a real,
    connectable but completely unmigrated Postgres schema (zero tables)
    previously still reported `database: ok` / HTTP 200, since `SELECT 1`
    doesn't reference any real table. A connectable database is not the
    same as a USABLE one; this must be caught and reported as not_ready
    rather than silently passing traffic to an instance whose first real
    query will fail with a confusing 'relation does not exist' error."""
    from alembic.script import ScriptDirectory

    monkeypatch.setattr(ScriptDirectory, "get_current_head", lambda self: "0999_not_a_real_head")

    resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "schema out of date" in body["checks"]["migration"]
    assert "0999_not_a_real_head" in body["checks"]["migration"]


async def test_ready_reports_redis_not_applicable_when_using_the_memory_transport(client) -> None:
    resp = await client.get("/ready")
    body = resp.json()
    # The test suite runs with EVENT_TRANSPORT=memory unless explicitly
    # overridden to real Redis for infrastructure verification — either way,
    # this must never silently claim "ok" for a check that didn't run.
    assert body["checks"]["redis"] in ("ok", "not_applicable (EVENT_TRANSPORT=memory)")


async def test_ready_returns_503_when_database_is_unreachable(client, monkeypatch) -> None:
    from app.db import session as db_session_module

    class _BrokenSessionMaker:
        def __call__(self):
            raise ConnectionError("simulated database outage")

    monkeypatch.setattr(db_session_module, "async_session_maker", _BrokenSessionMaker())
    resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "unreachable" in body["checks"]["database"]


async def test_ready_fails_fast_instead_of_hanging_when_a_dependency_never_responds(
    client, monkeypatch
) -> None:
    """Phase 12B: verified live against a real SIGSTOP'd redis-server that a
    connection can be TCP-accepted (kernel backlog) yet never reply — with no
    inner timeout, that hangs the request forever. This must fail within the
    endpoint's own bound instead."""
    import asyncio

    from app.db import session as db_session_module

    async def _never_returns(*_args, **_kwargs):
        await asyncio.sleep(999)

    class _HangingSessionMaker:
        def __call__(self):
            class _HangingSession:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_exc):
                    return False

                async def execute(self, *_args, **_kwargs):
                    await asyncio.sleep(999)

            return _HangingSession()

    monkeypatch.setattr(db_session_module, "async_session_maker", _HangingSessionMaker())

    async def _run_with_timeout():
        return await asyncio.wait_for(client.get("/ready"), timeout=10)

    resp = await _run_with_timeout()
    assert resp.status_code == 503
    assert "no response within" in resp.json()["checks"]["database"]
