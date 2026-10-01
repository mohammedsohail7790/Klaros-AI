"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 4
independently-opened sessions in app/api/v1/retention.py
(GET /retention/summary, GET /retention/analytics — each opens 2) now
stamp `SET LOCAL app.tenant_id`."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import set_tenant_context

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


async def _register(client: AsyncClient, org: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@requires_real_postgres
async def test_summary_and_analytics_set_tenant_context(monkeypatch, client: AsyncClient) -> None:
    import app.api.v1.retention as retention_module

    spy = _ContextSpy()
    monkeypatch.setattr(retention_module, "set_tenant_context", spy)

    token = await _register(client, "Retn Ctx Co", "owner@retnctx.com")
    headers = {"Authorization": f"Bearer {token}"}

    summary = await client.get("/api/v1/retention/summary", headers=headers)
    assert summary.status_code == 200

    analytics = await client.get("/api/v1/retention/analytics", headers=headers)
    assert analytics.status_code == 200

    assert len(spy.calls) >= 4
    tenant_id = spy.calls[0][0]
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
