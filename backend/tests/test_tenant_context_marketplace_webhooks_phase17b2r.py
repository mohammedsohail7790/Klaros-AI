"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in app/api/v1/marketplace_webhooks.py
(marketplace_lead_webhook) now stamps `SET LOCAL app.tenant_id`. Follows
tests/test_marketplace_lead_webhooks.py's own setup pattern (real
HTTP, real HMAC signature). tenant_id here is the path parameter, but it
is only trusted (and only reaches the session) after the tenant's own
connected webhook_secret verifies the signature — never trusted on its
own."""

import hashlib
import hmac
import json
import uuid

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

_SECRET = "test-marketplace-shared-secret"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _register(client: AsyncClient, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Mkt Webhook Ctx Co", "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["tokens"]["access_token"], uuid.UUID(data["user"]["tenant_id"])


async def _connect_provider(client: AsyncClient, token: str, provider: str) -> None:
    resp = await client.post(
        f"/api/v1/integrations/connections/{provider}/connect",
        json={"credential": {"webhook_secret": _SECRET}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


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
async def test_marketplace_lead_webhook_sets_tenant_context(monkeypatch, client: AsyncClient) -> None:
    import app.api.v1.marketplace_webhooks as marketplace_webhooks_module

    spy = _ContextSpy()
    monkeypatch.setattr(marketplace_webhooks_module, "set_tenant_context", spy)

    token, tenant_id = await _register(client, "marketplace-ctx@example.com")
    await _connect_provider(client, token, "angi")

    payload = {"id": "angi-ctx-lead-1", "name": "Ctx Tester", "phone": "555-000-1111", "email": "ctx@example.com"}
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_id}", content=body, headers={"X-Klaros-Signature": _sign(body)}
    )
    assert resp.status_code == 200, resp.text

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_marketplace_lead_webhook_never_crosses_tenant_context(monkeypatch, client: AsyncClient) -> None:
    """Two different tenants' webhook deliveries must each stamp their OWN
    tenant_id, never leaking the other tenant's id into the wrong call —
    the cross-tenant-denial proof required by this phase's methodology."""
    import app.api.v1.marketplace_webhooks as marketplace_webhooks_module

    spy = _ContextSpy()
    monkeypatch.setattr(marketplace_webhooks_module, "set_tenant_context", spy)

    token_a, tenant_a = await _register(client, "marketplace-ctx-a@example.com")
    await _connect_provider(client, token_a, "angi")
    token_b, tenant_b = await _register(client, "marketplace-ctx-b@example.com")
    await _connect_provider(client, token_b, "angi")

    for tenant_id, name in ((tenant_a, "A Tester"), (tenant_b, "B Tester")):
        payload = {"id": f"angi-ctx-lead-{tenant_id}", "name": name, "phone": "555-000-2222", "email": "ctx2@example.com"}
        body = json.dumps(payload).encode()
        resp = await client.post(
            f"/api/v1/webhooks/marketplace/angi/{tenant_id}", content=body, headers={"X-Klaros-Signature": _sign(body)}
        )
        assert resp.status_code == 200, resp.text

    assert len(spy.calls) == 2
    called_tenants = {t for t, _ in spy.calls}
    assert called_tenants == {tenant_a, tenant_b}
    for called_tenant, readback in spy.calls:
        # each call's readback must match THAT call's own tenant, never the other one
        assert readback == str(called_tenant)
    assert tenant_a != tenant_b
