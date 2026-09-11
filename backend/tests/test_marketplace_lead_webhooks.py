"""Marketplace lead-ingestion webhooks (Angi/Thumbtack/Nextdoor) — see
app/integrations/marketplace_adapters.py and
app/api/v1/marketplace_webhooks.py for why this verifies a Klaros-defined
HMAC scheme (none of these providers publishes a real one) rather than
pretending to speak each provider's actual, undocumented API."""

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.crm import Lead

pytestmark = pytest.mark.asyncio

_SECRET = "test-marketplace-shared-secret"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _register(client, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Marketplace Test Co", "full_name": "Owner", "email": email,
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["tokens"]["access_token"], uuid.UUID(data["user"]["tenant_id"])


async def _connect_provider(client, token: str, provider: str, secret: str = _SECRET, field_map: dict | None = None) -> None:
    credential = {"webhook_secret": secret}
    if field_map is not None:
        credential["field_map"] = json.dumps(field_map)
    resp = await client.post(
        f"/api/v1/integrations/connections/{provider}/connect",
        json={"credential": credential},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def test_webhook_rejected_when_provider_not_configured(client) -> None:
    _token, tenant_id = await _register(client, "marketplace-unconfigured@example.com")
    body = json.dumps({"id": "lead-1", "name": "Jane Doe"}).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_id}",
        content=body,
        headers={"X-Klaros-Signature": _sign(body)},
    )
    assert resp.status_code == 503


async def test_unknown_provider_returns_404(client) -> None:
    _token, tenant_id = await _register(client, "marketplace-unknown-provider@example.com")
    body = b"{}"
    resp = await client.post(f"/api/v1/webhooks/marketplace/not-a-real-provider/{tenant_id}", content=body)
    assert resp.status_code == 404


async def test_invalid_signature_is_rejected(client) -> None:
    token, tenant_id = await _register(client, "marketplace-badsig@example.com")
    await _connect_provider(client, token, "angi")
    body = json.dumps({"id": "lead-1", "name": "Jane Doe"}).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_id}",
        content=body,
        headers={"X-Klaros-Signature": "bogus"},
    )
    assert resp.status_code == 400


async def test_angi_default_field_map_creates_a_real_lead(client) -> None:
    token, tenant_id = await _register(client, "marketplace-angi@example.com")
    await _connect_provider(client, token, "angi")

    payload = {
        "id": "angi-lead-42", "name": "Jane Doe", "phone": "555-111-2222", "email": "jane@example.com",
        "location": "Austin, TX", "service": "Plumbing", "message": "Leaky faucet, need repair soon",
    }
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_id}",
        content=body,
        headers={"X-Klaros-Signature": _sign(body)},
    )
    assert resp.status_code == 200, resp.text
    lead_id = resp.json()["lead_id"]

    async with async_session_maker() as session:
        lead = (await session.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))).scalar_one()
    assert lead.tenant_id == tenant_id
    assert lead.source == "MARKETPLACE"
    assert lead.source_detail == "angi"
    assert lead.name == "Jane Doe"
    assert lead.phone == "555-111-2222"
    assert lead.service_requested == "Plumbing"


async def test_thumbtack_nested_default_field_map_creates_a_real_lead(client) -> None:
    token, tenant_id = await _register(client, "marketplace-thumbtack@example.com")
    await _connect_provider(client, token, "thumbtack")

    payload = {
        "id": "tt-99", "customer": {"name": "John Smith", "phone": "555-333-4444", "email": "john@example.com", "zip_code": "78701"},
        "category": "Electrical", "details": "Need an outlet installed",
    }
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/thumbtack/{tenant_id}",
        content=body,
        headers={"X-Klaros-Signature": _sign(body)},
    )
    assert resp.status_code == 200, resp.text

    async with async_session_maker() as session:
        lead = (
            await session.execute(select(Lead).where(Lead.id == uuid.UUID(resp.json()["lead_id"])))
        ).scalar_one()
    assert lead.source_detail == "thumbtack"
    assert lead.name == "John Smith"
    assert lead.service_requested == "Electrical"


async def test_custom_field_map_overrides_the_default(client) -> None:
    token, tenant_id = await _register(client, "marketplace-custommap@example.com")
    await _connect_provider(
        client, token, "nextdoor",
        field_map={"external_lead_id": "ref", "name": "who.name", "phone": "who.phone"},
    )

    payload = {"ref": "nd-7", "who": {"name": "Alex Rivera", "phone": "555-777-8888"}}
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/nextdoor/{tenant_id}",
        content=body,
        headers={"X-Klaros-Signature": _sign(body)},
    )
    assert resp.status_code == 200, resp.text
    async with async_session_maker() as session:
        lead = (
            await session.execute(select(Lead).where(Lead.id == uuid.UUID(resp.json()["lead_id"])))
        ).scalar_one()
    assert lead.name == "Alex Rivera"
    assert lead.phone == "555-777-8888"


async def test_missing_required_field_is_rejected(client) -> None:
    token, tenant_id = await _register(client, "marketplace-missingfield@example.com")
    await _connect_provider(client, token, "angi")
    payload = {"id": "angi-lead-incomplete"}  # no name
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_id}",
        content=body,
        headers={"X-Klaros-Signature": _sign(body)},
    )
    assert resp.status_code == 422


async def test_duplicate_external_lead_id_does_not_duplicate_the_lead(client) -> None:
    token, tenant_id = await _register(client, "marketplace-dedup@example.com")
    await _connect_provider(client, token, "angi")
    payload = {"id": "angi-lead-dup", "name": "Jane Doe", "email": "jane@example.com"}
    body = json.dumps(payload).encode()
    headers = {"X-Klaros-Signature": _sign(body)}

    resp1 = await client.post(f"/api/v1/webhooks/marketplace/angi/{tenant_id}", content=body, headers=headers)
    resp2 = await client.post(f"/api/v1/webhooks/marketplace/angi/{tenant_id}", content=body, headers=headers)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp2.json()["deduplicated"] is True

    async with async_session_maker() as session:
        leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(leads) == 1


async def test_marketplace_lead_never_crosses_tenants(client) -> None:
    token_a, tenant_a = await _register(client, "marketplace-tenant-a@example.com")
    _token_b, tenant_b = await _register(client, "marketplace-tenant-b@example.com")
    await _connect_provider(client, token_a, "angi")

    payload = {"id": "angi-lead-cross", "name": "Jane Doe", "email": "jane@example.com"}
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_a}", content=body, headers={"X-Klaros-Signature": _sign(body)}
    )
    assert resp.status_code == 200

    async with async_session_maker() as session:
        tenant_b_leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_b))).scalars().all()
    assert tenant_b_leads == []


async def test_a_tenant_cannot_forge_another_tenants_secret(client) -> None:
    """Tenant A configures secret S1; a caller signs with S2 (a guess/leak
    of some OTHER tenant's secret) — must still be rejected for tenant A."""
    token_a, tenant_a = await _register(client, "marketplace-secret-a@example.com")
    await _connect_provider(client, token_a, "angi", secret="tenant-a-secret")

    payload = {"id": "angi-lead-forge", "name": "Jane Doe"}
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/v1/webhooks/marketplace/angi/{tenant_a}",
        content=body,
        headers={"X-Klaros-Signature": _sign(body, secret="tenant-b-secret")},
    )
    assert resp.status_code == 400
