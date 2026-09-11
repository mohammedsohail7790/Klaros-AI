"""Public, unauthenticated lead intake (app/api/v1/public_leads.py) — closes
a real gap: the only lead-creation endpoint before this session required a
logged-in tenant user, so an actual website visitor or embedded chat widget
had no way to submit a lead. See ARCHITECTURE_TRACEABILITY.md."""

import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.crm import Lead

pytestmark = pytest.mark.asyncio


async def _register(client, email: str) -> uuid.UUID:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Public Lead Test Co", "full_name": "Owner", "email": email,
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["user"]["tenant_id"])


async def test_unknown_tenant_returns_404(client) -> None:
    resp = await client.post(
        f"/api/v1/public/leads/{uuid.uuid4()}",
        json={"name": "Jane Doe", "source": "WEB", "email": "jane@example.com"},
    )
    assert resp.status_code == 404


async def test_web_form_creates_a_real_lead(client) -> None:
    tenant_id = await _register(client, "public-lead-web@example.com")

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={
            "name": "Jane Doe", "source": "WEB", "email": "jane@example.com",
            "service_requested": "Plumbing repair", "description": "Leaky faucet",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["lead_id"]

    async with async_session_maker() as session:
        lead = (
            await session.execute(select(Lead).where(Lead.id == uuid.UUID(body["lead_id"])))
        ).scalar_one()
    assert lead.tenant_id == tenant_id
    assert lead.source == "WEB"
    assert lead.name == "Jane Doe"
    assert lead.source_detail == "public_web_form"
    # Owner-only fields must never be settable by an anonymous caller.
    assert lead.assigned_user_id is None
    assert lead.estimated_value is None


async def test_chat_widget_source_is_accepted(client) -> None:
    tenant_id = await _register(client, "public-lead-chat@example.com")

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={"name": "Chat Visitor", "source": "CHAT", "phone": "+15551234567"},
    )
    assert resp.status_code == 201, resp.text
    async with async_session_maker() as session:
        lead = (
            await session.execute(select(Lead).where(Lead.id == uuid.UUID(resp.json()["lead_id"])))
        ).scalar_one()
    assert lead.source == "CHAT"
    assert lead.source_detail == "public_chat_form"


async def test_source_outside_web_or_chat_is_rejected(client) -> None:
    tenant_id = await _register(client, "public-lead-badsource@example.com")
    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={"name": "Jane Doe", "source": "PHONE", "email": "jane@example.com"},
    )
    assert resp.status_code == 422


async def test_missing_phone_and_email_is_rejected(client) -> None:
    tenant_id = await _register(client, "public-lead-noreach@example.com")
    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={"name": "Jane Doe", "source": "WEB"},
    )
    assert resp.status_code == 422


async def test_honeypot_field_silently_drops_the_submission(client) -> None:
    tenant_id = await _register(client, "public-lead-honeypot@example.com")
    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={
            "name": "Bot", "source": "WEB", "email": "bot@example.com",
            "website": "http://spam.example.com",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["received"] is True
    assert body["lead_id"] is None

    async with async_session_maker() as session:
        leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert leads == []


async def test_idempotency_key_prevents_duplicate_leads(client) -> None:
    tenant_id = await _register(client, "public-lead-idem@example.com")
    payload = {
        "name": "Jane Doe", "source": "WEB", "email": "jane@example.com",
        "idempotency_key": "widget-submit-abc123",
    }
    resp1 = await client.post(f"/api/v1/public/leads/{tenant_id}", json=payload)
    resp2 = await client.post(f"/api/v1/public/leads/{tenant_id}", json=payload)
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["lead_id"] == resp2.json()["lead_id"]

    async with async_session_maker() as session:
        leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(leads) == 1


async def test_public_lead_never_crosses_tenants(client) -> None:
    tenant_a = await _register(client, "public-lead-tenant-a@example.com")
    tenant_b = await _register(client, "public-lead-tenant-b@example.com")

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_a}",
        json={"name": "Jane Doe", "source": "WEB", "email": "jane@example.com"},
    )
    assert resp.status_code == 201

    async with async_session_maker() as session:
        tenant_b_leads = (
            await session.execute(select(Lead).where(Lead.tenant_id == tenant_b))
        ).scalars().all()
    assert tenant_b_leads == []
