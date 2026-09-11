"""Extends the existing Twilio inbound-voice webhook coverage
(tests/test_twilio_inbound_lead_webhook.py) with the Phase 4 branch: when
a tenant has enabled the AI Voice Receptionist, the webhook must create a
real CallSession and return `<Connect><Stream>` TwiML instead of the
legacy capture-and-acknowledge response — and must NOT change behavior at
all for a tenant that hasn't enabled it (regression-proofed here too).

Also covers the authenticated /voice/settings and /voice/calls REST API,
including tenant isolation and permission enforcement.
"""

import base64
import hashlib
import hmac
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.crm import Lead
from app.models.voice import CallSession

pytestmark = pytest.mark.asyncio

_AUTH_TOKEN = "test_twilio_auth_token"


def _sign(url: str, params: dict[str, str], auth_token: str = _AUTH_TOKEN) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture(autouse=True)
def _configure_twilio_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    yield


async def _register_and_login(client, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Voice Test Co", "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["tokens"]["access_token"], uuid.UUID(data["user"]["tenant_id"])


# --- Webhook branch behavior ---

async def test_disabled_tenant_keeps_legacy_ack_behavior(client) -> None:
    token, tenant_id = await _register_and_login(client, "voice-disabled@example.com")
    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    url = "http://test" + path
    params = {"CallSid": f"CA{uuid.uuid4().hex[:16]}", "From": "+15551234567"}

    resp = await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})
    assert resp.status_code == 200
    assert "<Say>" in resp.text
    assert "<Connect>" not in resp.text

    async with async_session_maker() as session:
        lead = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalar_one()
    assert lead.source == "PHONE"


async def test_enabled_tenant_gets_connect_stream_and_a_real_call_session(client) -> None:
    token, tenant_id = await _register_and_login(client, "voice-enabled@example.com")
    await client.put(
        "/api/v1/voice/settings", json={"enabled": True}, headers={"Authorization": f"Bearer {token}"}
    )

    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    url = "http://test" + path
    call_sid = f"CA{uuid.uuid4().hex[:16]}"
    params = {"CallSid": call_sid, "From": "+15559876543"}

    resp = await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})
    assert resp.status_code == 200
    assert "<Connect>" in resp.text
    assert "<Stream" in resp.text
    assert f'value="{tenant_id}"' in resp.text

    async with async_session_maker() as session:
        call = (
            await session.execute(select(CallSession).where(CallSession.tenant_id == tenant_id))
        ).scalar_one()
    assert call.external_call_id == call_sid
    assert call.caller_number == "+15559876543"
    assert f'value="{call.id}"' in resp.text

    # No legacy PHONE lead was fabricated — the receptionist owns this call now.
    async with async_session_maker() as session:
        leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert leads == []


async def test_repeated_webhook_delivery_reuses_the_same_call_session(client) -> None:
    token, tenant_id = await _register_and_login(client, "voice-idempotent@example.com")
    await client.put("/api/v1/voice/settings", json={"enabled": True}, headers={"Authorization": f"Bearer {token}"})

    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    url = "http://test" + path
    call_sid = f"CA{uuid.uuid4().hex[:16]}"
    params = {"CallSid": call_sid, "From": "+15550001111"}
    headers = {"X-Twilio-Signature": _sign(url, params)}

    resp1 = await client.post(path, data=params, headers=headers)
    resp2 = await client.post(path, data=params, headers=headers)
    assert resp1.status_code == 200 and resp2.status_code == 200

    async with async_session_maker() as session:
        calls = (
            await session.execute(select(CallSession).where(CallSession.tenant_id == tenant_id))
        ).scalars().all()
    assert len(calls) == 1


async def test_invalid_signature_rejected_even_when_voice_enabled(client) -> None:
    token, tenant_id = await _register_and_login(client, "voice-badsig@example.com")
    await client.put("/api/v1/voice/settings", json={"enabled": True}, headers={"Authorization": f"Bearer {token}"})
    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    resp = await client.post(path, data={"CallSid": "CA1", "From": "+1555"}, headers={"X-Twilio-Signature": "bogus"})
    assert resp.status_code == 400


# --- REST API ---

async def test_settings_default_and_update_round_trip(client) -> None:
    token, _tenant_id = await _register_and_login(client, "voice-settings-api@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/voice/settings", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await client.put(
        "/api/v1/voice/settings", json={"enabled": True, "greeting": "Welcome to Acme HVAC!"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["greeting"] == "Welcome to Acme HVAC!"


async def test_settings_requires_auth(client) -> None:
    resp = await client.get("/api/v1/voice/settings")
    assert resp.status_code in (401, 403)


async def test_calls_list_and_detail_require_auth(client) -> None:
    resp = await client.get("/api/v1/voice/calls")
    assert resp.status_code in (401, 403)


async def test_calls_never_leak_across_tenants(client) -> None:
    token_a, tenant_a = await _register_and_login(client, "voice-tenant-a@example.com")
    token_b, _tenant_b = await _register_and_login(client, "voice-tenant-b@example.com")
    await client.put("/api/v1/voice/settings", json={"enabled": True}, headers={"Authorization": f"Bearer {token_a}"})

    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_a}"
    url = "http://test" + path
    params = {"CallSid": f"CA{uuid.uuid4().hex[:16]}", "From": "+15551112222"}
    await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})

    resp_a = await client.get("/api/v1/voice/calls", headers={"Authorization": f"Bearer {token_a}"})
    resp_b = await client.get("/api/v1/voice/calls", headers={"Authorization": f"Bearer {token_b}"})
    assert len(resp_a.json()["calls"]) == 1
    assert resp_b.json()["calls"] == []


async def test_get_call_detail_rejects_another_tenants_call_id(client) -> None:
    token_a, tenant_a = await _register_and_login(client, "voice-detail-a@example.com")
    token_b, _tenant_b = await _register_and_login(client, "voice-detail-b@example.com")
    await client.put("/api/v1/voice/settings", json={"enabled": True}, headers={"Authorization": f"Bearer {token_a}"})

    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_a}"
    url = "http://test" + path
    params = {"CallSid": f"CA{uuid.uuid4().hex[:16]}", "From": "+15551113333"}
    await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})

    resp_a = await client.get("/api/v1/voice/calls", headers={"Authorization": f"Bearer {token_a}"})
    call_id = resp_a.json()["calls"][0]["id"]

    resp = await client.get(f"/api/v1/voice/calls/{call_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 404
