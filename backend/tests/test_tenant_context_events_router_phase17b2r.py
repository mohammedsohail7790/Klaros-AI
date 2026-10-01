"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in app/api/v1/events.py
(GET /events/{event_id}) now stamps `SET LOCAL app.tenant_id`."""

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
async def test_get_event_sets_tenant_context(monkeypatch, client: AsyncClient) -> None:
    import app.api.v1.events as events_module

    spy = _ContextSpy()
    monkeypatch.setattr(events_module, "set_tenant_context", spy)

    token = await _register(client, "Event Ctx Co", "owner@eventctx.com")
    headers = {"Authorization": f"Bearer {token}"}

    published = await client.post(
        "/api/v1/events", json={"event_type": "test.router_ctx_probe", "payload": {}}, headers=headers
    )
    assert published.status_code == 201, published.text
    event_id = published.json()["id"]

    resp = await client.get(f"/api/v1/events/{event_id}", headers=headers)
    assert resp.status_code == 200

    assert len(spy.calls) >= 1
    tenant_id = spy.calls[0][0]
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_event_never_fetchable_by_tenant_b(client: AsyncClient) -> None:
    token_a = await _register(client, "Event Ctx A Co", "owner@eventctxa.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    published = await client.post(
        "/api/v1/events", json={"event_type": "test.cross_tenant_probe", "payload": {}}, headers=headers_a
    )
    assert published.status_code == 201
    event_id = published.json()["id"]

    token_b = await _register(client, "Event Ctx B Co", "owner@eventctxb.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    resp_b = await client.get(f"/api/v1/events/{event_id}", headers=headers_b)
    assert resp_b.status_code == 404
