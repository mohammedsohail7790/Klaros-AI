"""SMTP as a real e-mail provider (Brevo, Gmail, Zoho, ...): it sends through the configured account, logs every attempt, never reports a failed send as delivered, and every part of Klaros
that asks "is e-mail configured?" gives the same answer."""

import smtplib

import pytest
from sqlalchemy import select

from app.communications import smtp_client
from app.communications.base import MessageTemplate
from app.communications.email_config import email_configured, sendgrid_configured, smtp_configured
from app.communications.factory import _build_provider
from app.communications.sendgrid_adapter import SendGridEmailAdapter
from app.communications.smtp_adapter import SmtpEmailAdapter
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.communication import CommunicationLog
from tests.test_halla_integration import _register, _tenant_id

pytestmark = pytest.mark.asyncio


class _FakeSMTP:
    instances: list = []
    fail_login = False

    def __init__(self, host, port, timeout=None) -> None:
        self.host, self.port, self.timeout, self.calls, self.sent = host, port, timeout, [], []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        if _FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.calls.append(("login", user))

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    s = get_settings()
    _FakeSMTP.instances, _FakeSMTP.fail_login = [], False
    for k, v in {"SMTP_HOST": "smtp-relay.example.com", "SMTP_PORT": 587, "SMTP_USERNAME": "user@example.com", "SMTP_PASSWORD": "smtp-secret", "SMTP_FROM_EMAIL": "hello@klaros.example",
                 "SENDGRID_API_KEY": None, "SENDGRID_FROM_EMAIL": None, "TWILIO_ACCOUNT_SID": None}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    return s


async def test_one_answer_to_is_email_configured(smtp, monkeypatch) -> None:
    assert smtp_configured() and not sendgrid_configured() and email_configured()
    monkeypatch.setattr(smtp, "SMTP_PASSWORD", None)  # a half-set account is not configured
    assert not smtp_configured() and not email_configured()
    monkeypatch.setattr(smtp, "SENDGRID_API_KEY", "k")
    monkeypatch.setattr(smtp, "SENDGRID_FROM_EMAIL", "from@x.example")
    assert email_configured() and sendgrid_configured()


async def test_a_message_goes_through_starttls_login_and_the_configured_from_address(smtp) -> None:
    await smtp_client.send_via_smtp(to="clinic@example.com", subject="Hello", body="Body text", from_name="Klaros AI")
    server = _FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp-relay.example.com", 587)
    assert server.calls == ["starttls", ("login", "user@example.com")]
    msg = server.sent[0]
    assert msg["To"] == "clinic@example.com" and msg["Subject"] == "Hello" and "hello@klaros.example" in msg["From"] and "Body text" in msg.get_content()


async def test_port_465_uses_a_direct_ssl_connection_without_starttls(smtp, monkeypatch) -> None:
    monkeypatch.setattr(smtp, "SMTP_PORT", 465)
    await smtp_client.send_via_smtp(to="clinic@example.com", subject="s", body="b")
    assert _FakeSMTP.instances[0].calls == [("login", "user@example.com")]


async def test_the_adapter_logs_every_send_and_reports_a_failed_one_as_failed(client, smtp) -> None:
    token = await _register(client, "Smtp Co", "smtpco@example.com")
    tid = await _tenant_id(client, token)
    adapter = SmtpEmailAdapter(async_session_maker)
    assert await adapter.send_email(tid, to="a@example.com", subject="Hi", body="Hello", template=MessageTemplate.REFERRAL_INVITATION) is True
    _FakeSMTP.fail_login = True
    assert await adapter.send_email(tid, to="b@example.com", subject="Hi", body="Hello", template=MessageTemplate.REFERRAL_INVITATION) is False
    assert await adapter.send_sms(tid, to="+15550000000", body="x", template=MessageTemplate.REFERRAL_INVITATION) is False
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        rows = {(r.recipient, r.status, r.provider) for r in (await s.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tid))).scalars()}
    assert rows == {("a@example.com", "SENT", "smtp"), ("b@example.com", "FAILED_SMTP", "smtp"), ("+15550000000", "FAILED_NO_SMS_PROVIDER", "smtp")}


async def test_the_factory_uses_smtp_when_only_smtp_is_set_and_sendgrid_when_both_are(smtp, monkeypatch) -> None:
    provider = _build_provider(async_session_maker)
    assert type(getattr(provider, "_email_provider", None)) is SmtpEmailAdapter
    monkeypatch.setattr(smtp, "SENDGRID_API_KEY", "k")
    monkeypatch.setattr(smtp, "SENDGRID_FROM_EMAIL", "from@x.example")
    assert type(getattr(_build_provider(async_session_maker), "_email_provider", None)) is SendGridEmailAdapter  # SendGrid wins


async def test_account_emails_such_as_a_password_reset_also_use_smtp(smtp) -> None:
    from app.services.system_mailer import email_is_configured, send_system_email

    assert email_is_configured() is True
    assert await send_system_email(to="owner@example.com", subject="Reset", body="link") is True
    assert _FakeSMTP.instances[0].sent[0]["To"] == "owner@example.com"
    _FakeSMTP.fail_login = True
    assert await send_system_email(to="owner@example.com", subject="Reset", body="link") is False  # a failed send is never reported as sent


async def test_the_setup_plan_counts_smtp_as_working_email(client, smtp) -> None:
    from tests.test_halla_integration import _h

    token = await _register(client, "Plan Smtp Co", "plansmtp@example.com")
    steps = {s["key"]: s for s in (await client.get("/api/v1/setup-plan", headers=_h(token))).json()["steps"]}
    assert steps["email"]["state"] == "done"
