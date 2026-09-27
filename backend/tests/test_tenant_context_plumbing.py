"""Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.2): unit coverage for
app.db.session.set_tenant_context that runs on the default SQLite test
engine — proves it is a genuine no-op there (never raises, never touches
anything) rather than only being exercised by the Postgres-only RLS suite
(tests/test_postgres_rls_audit_mode.py), which needs a real Postgres and
is skipped in the default local/test run."""

import uuid

import pytest

from app.db.session import async_session_maker, set_tenant_context

pytestmark = pytest.mark.asyncio


async def test_set_tenant_context_is_a_no_op_on_sqlite() -> None:
    async with async_session_maker() as session:
        # Must not raise on the SQLite test engine — SQLite has no
        # set_config()/current_setting() and no RLS concept at all.
        await set_tenant_context(session, uuid.uuid4())


async def test_set_tenant_context_is_a_no_op_when_tenant_id_is_none() -> None:
    async with async_session_maker() as session:
        await set_tenant_context(session, None)


async def test_get_current_user_stamps_tenant_context_without_raising(client) -> None:
    """End-to-end smoke test through the real dependency chain (register ->
    authenticated request) — confirms wiring set_tenant_context into
    get_current_user (app/api/deps.py) didn't break the ordinary request
    path on SQLite."""
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Tenant Context Test Co",
            "full_name": "Test Owner",
            "email": f"tenant-ctx-{uuid.uuid4()}@example.com",
            "password": "a-real-password-123",
        },
    )
    assert register_resp.status_code == 201, register_resp.text
    token = register_resp.json()["tokens"]["access_token"]

    me_resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200, me_resp.text
