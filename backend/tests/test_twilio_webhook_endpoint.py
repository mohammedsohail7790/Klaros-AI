"""Phase 12C: Twilio status-callback webhook endpoint — signature
validation, dedup, and CommunicationLog status update, fully testable
without real Twilio credentials since this test controls both the signer
and the configured auth token."""

import base64
import hashlib
import hmac
import uuid

import pytest

from app.core.config import get_settings
from app.models.communication import CommunicationLog

pytestmark = pytest.mark.asyncio

_AUTH_TOKEN = "test_twilio_auth_token"
_WEBHOOK_PATH = "/api/v1/webhooks/twilio/status"
_BASE_URL = "http://test" + _WEBHOOK_PATH


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


async def test_invalid_signature_is_rejected(client) -> None:
    params = {"MessageSid": "SM123", "MessageStatus": "delivered"}
    resp = await client.post(_WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": "bogus"})
    assert resp.status_code == 400


async def test_missing_signature_is_rejected(client) -> None:
    params = {"MessageSid": "SM123", "MessageStatus": "delivered"}
    resp = await client.post(_WEBHOOK_PATH, data=params)
    assert resp.status_code == 400


async def test_valid_signature_updates_matching_communication_log(client) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    message_sid = f"SM{uuid.uuid4().hex[:16]}"
    async with async_session_maker() as session:
        session.add(
            CommunicationLog(
                tenant_id=tenant_id, channel="SMS", template="appointment_reminder", recipient="+15551234567",
                external_id=message_sid, body="hi", status="SENT", provider="twilio",
            )
        )
        await session.commit()

    params = {"MessageSid": message_sid, "MessageStatus": "delivered"}
    signature = _sign(_BASE_URL, params)
    resp = await client.post(_WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    async with async_session_maker() as session:
        from sqlalchemy import select

        row = (
            await session.execute(select(CommunicationLog).where(CommunicationLog.external_id == message_sid))
        ).scalar_one()
        assert row.status == "TWILIO_DELIVERED"


async def test_duplicate_status_callback_is_ignored(client) -> None:
    message_sid = f"SM{uuid.uuid4().hex[:16]}"
    params = {"MessageSid": message_sid, "MessageStatus": "sent"}
    signature = _sign(_BASE_URL, params)
    headers = {"X-Twilio-Signature": signature}

    resp1 = await client.post(_WEBHOOK_PATH, data=params, headers=headers)
    assert resp1.json()["status"] == "processed"

    resp2 = await client.post(_WEBHOOK_PATH, data=params, headers=headers)
    assert resp2.json()["status"] == "duplicate_ignored"


async def test_different_status_for_the_same_message_is_not_a_duplicate(client) -> None:
    """queued -> sent -> delivered are genuinely different real-world
    events for the same MessageSid, not retries of the same delivery."""
    message_sid = f"SM{uuid.uuid4().hex[:16]}"

    params_sent = {"MessageSid": message_sid, "MessageStatus": "sent"}
    resp1 = await client.post(
        _WEBHOOK_PATH, data=params_sent, headers={"X-Twilio-Signature": _sign(_BASE_URL, params_sent)}
    )
    assert resp1.json()["status"] == "processed"

    params_delivered = {"MessageSid": message_sid, "MessageStatus": "delivered"}
    resp2 = await client.post(
        _WEBHOOK_PATH, data=params_delivered, headers={"X-Twilio-Signature": _sign(_BASE_URL, params_delivered)}
    )
    assert resp2.json()["status"] == "processed"
