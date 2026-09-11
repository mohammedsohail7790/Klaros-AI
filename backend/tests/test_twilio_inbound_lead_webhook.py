"""Inbound Twilio lead capture — genuinely absent before this: the
existing Twilio integration was outbound-SMS + delivery-status only.
Tenant resolution uses a tenant_id embedded in the webhook URL the tenant
pastes into their own Twilio console (a deliberate one-time admin action,
not attacker-controlled input) — the caller's raw phone number, genuinely
untrusted, is never used to derive tenant identity, only to identify/
match the resulting Lead via the same normalize_phone path every other
lead source already uses. Fully testable without real Twilio credentials
since this test controls both the signer and the configured auth token,
exactly like test_twilio_webhook_endpoint.py's status-callback tests."""

import base64
import hashlib
import hmac
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.crm import Lead

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


# --- Inbound SMS ---------------------------------------------------------

async def test_inbound_sms_invalid_signature_is_rejected(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-sms/{tenant_id}"
    params = {"MessageSid": "SM1", "From": "+15551234567", "Body": "Need a plumber"}
    resp = await client.post(path, data=params, headers={"X-Twilio-Signature": "bogus"})
    assert resp.status_code == 400


async def test_inbound_sms_creates_a_real_lead(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-sms/{tenant_id}"
    url = "http://test" + path
    message_sid = f"SM{uuid.uuid4().hex[:16]}"
    params = {"MessageSid": message_sid, "From": "+15551234567", "Body": "Need a plumber ASAP"}

    resp = await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})
    assert resp.status_code == 200
    assert "<Response>" in resp.text

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1
    lead = rows[0]
    assert lead.source == "TEXT"
    assert lead.phone == "+15551234567"
    assert lead.description == "Need a plumber ASAP"
    # No fabricated urgency/service — only what the message itself carried.
    assert lead.service_requested is None
    assert lead.idempotency_key == f"twilio-inbound-sms-{message_sid}"


async def test_inbound_sms_never_fabricates_a_name_for_an_unknown_caller(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-sms/{tenant_id}"
    url = "http://test" + path
    params = {"MessageSid": f"SM{uuid.uuid4().hex[:16]}", "From": "+15559998888", "Body": "hi"}

    await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        lead = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalar_one()
    assert "5559998888" in lead.name
    assert lead.name != ""


async def test_duplicate_inbound_sms_never_creates_a_second_lead(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-sms/{tenant_id}"
    url = "http://test" + path
    message_sid = f"SM{uuid.uuid4().hex[:16]}"
    params = {"MessageSid": message_sid, "From": "+15551234567", "Body": "Need a plumber"}
    headers = {"X-Twilio-Signature": _sign(url, params)}

    resp1 = await client.post(path, data=params, headers=headers)
    assert resp1.status_code == 200
    resp2 = await client.post(path, data=params, headers=headers)
    assert resp2.status_code == 200

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1


async def test_inbound_sms_leads_are_tenant_isolated(client) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    message_sid = f"SM{uuid.uuid4().hex[:16]}"
    params = {"MessageSid": message_sid, "From": "+15551234567", "Body": "hi"}

    path_a = f"/api/v1/webhooks/twilio/inbound-sms/{tenant_a}"
    await client.post(path_a, data=params, headers={"X-Twilio-Signature": _sign("http://test" + path_a, params)})

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        leads_a = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_a))).scalars().all()
        leads_b = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_b))).scalars().all()
    assert len(leads_a) == 1
    assert len(leads_b) == 0


# --- Inbound voice ---------------------------------------------------------

async def test_inbound_voice_invalid_signature_is_rejected(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    params = {"CallSid": "CA1", "From": "+15551234567"}
    resp = await client.post(path, data=params, headers={"X-Twilio-Signature": "bogus"})
    assert resp.status_code == 400


async def test_inbound_voice_creates_a_real_lead_and_returns_twiml_acknowledgement(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    url = "http://test" + path
    call_sid = f"CA{uuid.uuid4().hex[:16]}"
    params = {"CallSid": call_sid, "From": "+15557654321"}

    resp = await client.post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})
    assert resp.status_code == 200
    assert "<Say>" in resp.text

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        lead = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalar_one()
    assert lead.source == "PHONE"
    assert lead.phone == "+15557654321"
    assert lead.idempotency_key == f"twilio-inbound-voice-{call_sid}"


async def test_duplicate_inbound_call_never_creates_a_second_lead(client) -> None:
    tenant_id = uuid.uuid4()
    path = f"/api/v1/webhooks/twilio/inbound-voice/{tenant_id}"
    url = "http://test" + path
    call_sid = f"CA{uuid.uuid4().hex[:16]}"
    params = {"CallSid": call_sid, "From": "+15557654321"}
    headers = {"X-Twilio-Signature": _sign(url, params)}

    await client.post(path, data=params, headers=headers)
    await client.post(path, data=params, headers=headers)

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1
