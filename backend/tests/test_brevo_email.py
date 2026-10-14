"""Brevo's web API as a real e-mail provider: it works over HTTPS (so it works where outbound SMTP is blocked), logs every attempt, never reports a failed send as delivered, and is chosen
ahead of SMTP. No real request is made: an in-process mock transport answers."""

import json

import httpx
import pytest
from sqlalchemy import select

from app.communications import brevo_client
from app.communications.base import MessageTemplate
from app.communications.brevo_adapter import BrevoEmailAdapter
from app.communications.email_config import brevo_configured, email_configured
from app.communications.factory import _build_provider
from app.communications.sendgrid_adapter import SendGridEmailAdapter
from app.communications.smtp_adapter import SmtpEmailAdapter
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.communication import CommunicationLog
from tests.test_halla_integration import _h, _register, _tenant_id

pytestmark = pytest.mark.asyncio


class _Brevo:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 201

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"messageId": "<abc@brevo>"} if self.status < 300 else {"message": "Key not found"})


@pytest.fixture
def brevo(monkeypatch):
    s = get_settings()
    for k, v in {"BREVO_API_KEY": "api-key-for-test", "BREVO_FROM_EMAIL": "hello@klaros.example", "SMTP_FROM_EMAIL": None, "SMTP_HOST": None, "SENDGRID_API_KEY": None, "SENDGRID_FROM_EMAIL": None,
                 "TWILIO_ACCOUNT_SID": None}.items():
        monkeypatch.setattr(s, k, v)
    fake = _Brevo()
    monkeypatch.setattr(brevo_client, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


async def test_it_posts_to_the_brevo_api_over_https_with_the_key_and_the_verified_sender(brevo) -> None:
    mid = await brevo_client.send_via_brevo(to="clinic@example.com", subject="Hello", body="Body text", from_name="Klaros AI")
    req = brevo.requests[0]
    assert str(req.url) == "https://api.brevo.com/v3/smtp/email" and req.headers["api-key"] == "api-key-for-test"
    body = json.loads(req.content)
    assert body["sender"] == {"email": "hello@klaros.example", "name": "Klaros AI"} and body["to"] == [{"email": "clinic@example.com"}] and body["textContent"] == "Body text"
    assert mid == "<abc@brevo>"


async def test_the_sender_falls_back_to_the_smtp_from_address_so_only_the_key_is_new(brevo, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "BREVO_FROM_EMAIL", None)
    assert brevo_configured() is False  # no sender anywhere
    monkeypatch.setattr(s, "SMTP_FROM_EMAIL", "already-set@klaros.example")
    assert brevo_configured() and email_configured()
    await brevo_client.send_via_brevo(to="a@example.com", subject="s", body="b")
    assert json.loads(brevo.requests[0].content)["sender"]["email"] == "already-set@klaros.example"


async def test_a_rejected_key_raises_and_the_adapter_logs_and_reports_the_failure(client, brevo) -> None:
    token = await _register(client, "Brevo Co", "brevoco@example.com")
    tid = await _tenant_id(client, token)
    adapter = BrevoEmailAdapter(async_session_maker)
    assert await adapter.send_email(tid, to="ok@example.com", subject="Hi", body="Hello", template=MessageTemplate.REFERRAL_INVITATION) is True
    brevo.status = 401
    assert await adapter.send_email(tid, to="bad@example.com", subject="Hi", body="Hello", template=MessageTemplate.REFERRAL_INVITATION) is False
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        rows = {(r.recipient, r.status, r.provider) for r in (await s.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tid))).scalars()}
    assert rows == {("ok@example.com", "SENT", "brevo"), ("bad@example.com", "FAILED_BREVO", "brevo")}


async def test_brevo_is_chosen_ahead_of_smtp_and_after_sendgrid(brevo, monkeypatch) -> None:
    s = get_settings()
    for k, v in {"SMTP_HOST": "smtp.example.com", "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p", "SMTP_FROM_EMAIL": "f@x.example"}.items():
        monkeypatch.setattr(s, k, v)
    assert type(getattr(_build_provider(async_session_maker), "_email_provider", None)) is BrevoEmailAdapter  # both set: Brevo's web API wins over SMTP
    monkeypatch.setattr(s, "BREVO_API_KEY", None)
    assert type(getattr(_build_provider(async_session_maker), "_email_provider", None)) is SmtpEmailAdapter
    monkeypatch.setattr(s, "BREVO_API_KEY", "k")
    monkeypatch.setattr(s, "SENDGRID_API_KEY", "sg")
    monkeypatch.setattr(s, "SENDGRID_FROM_EMAIL", "s@x.example")
    assert type(getattr(_build_provider(async_session_maker), "_email_provider", None)) is SendGridEmailAdapter


async def test_account_emails_and_the_test_email_use_the_brevo_api(client, brevo) -> None:
    from app.services.system_mailer import send_system_email

    assert await send_system_email(to="owner@example.com", subject="Reset", body="link") is True
    assert json.loads(brevo.requests[0].content)["to"] == [{"email": "owner@example.com"}]
    brevo.status = 401
    assert await send_system_email(to="owner@example.com", subject="Reset", body="link") is False  # never reported as sent
    brevo.status = 201
    token = await _register(client, "Brevo Test Mail Co", "brevotestmail@example.com")
    ok = await client.post("/api/v1/setup-plan/test-email", headers=_h(token))
    assert ok.json() == {"sent": True, "to": "brevotestmail@example.com", "reason": None}
    steps = {s["key"]: s for s in (await client.get("/api/v1/setup-plan", headers=_h(token))).json()["steps"]}
    assert steps["email"]["state"] == "done"
