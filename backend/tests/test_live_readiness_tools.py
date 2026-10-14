"""What a business needs to run live: invite clinics to send their credentials, acknowledge each enquiry, and prove e-mail works. Everything reaches people only through the
configured e-mail provider, with consent respected, once, and honestly reported when it cannot send."""

import uuid

import pytest
from sqlalchemy import select

from app.api.tool_deps import get_tool_registry
from app.communications.base import SendResult, SendStatus
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.medical_tourism import Provider
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_consent_gate_intake import _enable_vertical
from tests.test_mt_referrals import _FakeChannel, _gated, _h, _patient, _provider, halla  # noqa: F401
from tests.test_halla_integration import _lead, _register, _tenant_id

pytestmark = pytest.mark.asyncio


def _ctx(tid):
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.AI, actor_id=uuid.uuid4(), role=Role.MANAGER)


@pytest.fixture
def email_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(s, "SMTP_USERNAME", "u")
    monkeypatch.setattr(s, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(s, "SMTP_FROM_EMAIL", "from@klaros.example")
    fake = _FakeChannel()
    monkeypatch.setattr("app.communications.factory.get_clinic_channel", lambda session_factory: fake)
    return fake


async def _set_provider(tid, pid, **fields):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = await s.get(Provider, uuid.UUID(pid))
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()


async def test_these_actions_wait_for_approval_by_default() -> None:
    for tool in ("medical_tourism.invite_clinic", "crm.acknowledge_enquiry"):
        assert DEFAULT_TOOL_POLICIES[tool] == ActionPolicy.APPROVAL_REQUIRED


async def test_a_clinic_with_an_address_is_listed_then_invited_once_and_never_told_about_a_patient(client, halla, email_on) -> None:  # noqa: F811
    token, tid = await _gated(client, "Invite Co", "inviteco@example.com")
    clinic = await _provider(client, token, "Unverified Smile", verified=False)
    no_address = await _provider(client, token, "No Address Clinic", verified=False)
    await _set_provider(tid, clinic["id"], contact_email="hello@smile.example")
    reg = get_tool_registry()
    work = await reg.execute("medical_tourism.list_referral_work", {}, _ctx(tid))
    assert [c["clinic"] for c in work.clinics_to_invite] == ["Unverified Smile"]  # only a clinic we can actually write to
    with pytest.raises(ToolApprovalRequiredError):
        await reg.execute("medical_tourism.invite_clinic", {"provider_id": clinic["id"]}, _ctx(tid))
    assert email_on.sent == []
    first = await reg.execute("medical_tourism.invite_clinic", {"provider_id": clinic["id"]}, _ctx(tid), skip_approval_gate=True)
    assert first.invited is True and email_on.sent[0]["to"] == "hello@smile.example"
    assert "patient information" in email_on.sent[0]["body"] and "licence" in email_on.sent[0]["body"]
    again = await reg.execute("medical_tourism.invite_clinic", {"provider_id": clinic["id"]}, _ctx(tid), skip_approval_gate=True)
    assert again.invited is False and "14 days" in again.reason and len(email_on.sent) == 1
    assert (await reg.execute("medical_tourism.list_referral_work", {}, _ctx(tid))).clinics_to_invite == []  # already asked
    none = await reg.execute("medical_tourism.invite_clinic", {"provider_id": no_address["id"]}, _ctx(tid), skip_approval_gate=True)
    assert none.invited is False and "no contact e-mail" in none.reason


async def test_a_verified_clinic_is_not_invited_and_nothing_is_sent_without_a_real_provider(client, halla, monkeypatch) -> None:  # noqa: F811
    s = get_settings()
    for k in ("SENDGRID_API_KEY", "SMTP_HOST"):
        monkeypatch.setattr(s, k, None)
    token, tid = await _gated(client, "No Mail Co", "nomailco@example.com")
    verified = await _provider(client, token, "Already Verified")
    await _set_provider(tid, verified["id"], contact_email="x@clinic.example")
    out = await get_tool_registry().execute("medical_tourism.invite_clinic", {"provider_id": verified["id"]}, _ctx(tid), skip_approval_gate=True)
    assert out.invited is False and "already verified" in out.reason
    pending = await _provider(client, token, "Pending Clinic", verified=False)
    await _set_provider(tid, pending["id"], contact_email="y@clinic.example")
    out = await get_tool_registry().execute("medical_tourism.invite_clinic", {"provider_id": pending["id"]}, _ctx(tid), skip_approval_gate=True)
    assert out.invited is False and "no real e-mail provider" in out.reason  # honest, never a pretend send


async def test_an_enquiry_is_acknowledged_once_only_with_consent_and_without_saying_what_was_asked(client, halla, email_on, monkeypatch) -> None:  # noqa: F811
    sent: list[dict] = []

    class _Guarded:
        async def deliver_email(self, tenant_id, *, to, subject, body, template, lead_id=None, **kw):
            sent.append({"to": to, "subject": subject, "body": body, "lead_id": lead_id})
            return SendResult(SendStatus.SENT)

    monkeypatch.setattr("app.communications.factory.get_communication_provider", lambda session_factory: _Guarded())
    token, tid = await _gated(client, "Ack Co", "ackco@example.com")
    lead = await _patient(client, token)
    reg = get_tool_registry()
    with pytest.raises(ToolApprovalRequiredError):
        await reg.execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid))
    assert sent == []
    out = await reg.execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)
    assert out.sent is True and len(sent) == 1 and "@example.com" in sent[0]["to"] and str(sent[0]["lead_id"]) == lead
    assert "received your enquiry" in sent[0]["body"] and "Ack Co" in sent[0]["subject"]
    again = await reg.execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)
    assert again.sent is False and "already" in again.reason and len(sent) == 1


async def test_a_blocked_consent_or_missing_provider_is_reported_not_hidden(client, halla, monkeypatch) -> None:  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "SENDGRID_API_KEY", None)
    monkeypatch.setattr(s, "SMTP_HOST", None)
    token, tid = await _gated(client, "Ack Closed Co", "ackclosed@example.com")
    lead = await _patient(client, token)
    out = await get_tool_registry().execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)
    assert out.sent is False and "no real e-mail provider" in out.reason
    monkeypatch.setattr(s, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(s, "SMTP_USERNAME", "u")
    monkeypatch.setattr(s, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(s, "SMTP_FROM_EMAIL", "f@x.example")

    class _Blocked:
        async def deliver_email(self, *a, **kw):
            return SendResult(SendStatus.BLOCKED_CONSENT, "no_contact_consent")

    monkeypatch.setattr("app.communications.factory.get_communication_provider", lambda session_factory: _Blocked())
    blocked = await get_tool_registry().execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)
    assert blocked.sent is False and "consent" in blocked.reason


async def test_the_test_email_goes_only_to_the_signed_in_persons_own_address_and_reports_honestly(client, monkeypatch) -> None:
    token = await _register(client, "Test Mail Co", "testmailco@example.com")
    s = get_settings()
    monkeypatch.setattr(s, "SENDGRID_API_KEY", None)
    monkeypatch.setattr(s, "SMTP_HOST", None)
    off = await client.post("/api/v1/setup-plan/test-email", json={"to": "someone-else@evil.example"}, headers=_h(token))
    assert off.status_code == 200 and off.json()["sent"] is False and "configured" in off.json()["reason"]
    monkeypatch.setattr(s, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(s, "SMTP_USERNAME", "u")
    monkeypatch.setattr(s, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(s, "SMTP_FROM_EMAIL", "f@x.example")
    delivered: list[str] = []

    async def fake_send(*, to, subject, body):
        delivered.append(to)
        return True

    monkeypatch.setattr("app.services.system_mailer.send_system_email", fake_send)
    ok = await client.post("/api/v1/setup-plan/test-email", json={"to": "someone-else@evil.example"}, headers=_h(token))
    assert ok.json() == {"sent": True, "to": "testmailco@example.com", "reason": None} and delivered == ["testmailco@example.com"]  # the request's address is ignored
    for _ in range(4):
        await client.post("/api/v1/setup-plan/test-email", headers=_h(token))
    assert (await client.post("/api/v1/setup-plan/test-email", headers=_h(token))).status_code == 429  # limited per hour
    assert (await client.post("/api/v1/setup-plan/test-email")).status_code == 401


async def test_a_refused_attempt_does_not_use_up_the_acknowledgement(client, halla, monkeypatch) -> None:  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "SENDGRID_API_KEY", None)
    monkeypatch.setattr(s, "SMTP_HOST", None)
    token, tid = await _gated(client, "Retry Ack Co", "retryack@example.com")
    lead = await _patient(client, token)
    reg = get_tool_registry()
    assert (await reg.execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)).sent is False  # no provider yet
    sent: list[str] = []

    class _Guarded:
        async def deliver_email(self, tenant_id, *, to, subject, body, template, lead_id=None, **kw):
            sent.append(to)
            return SendResult(SendStatus.SENT)

    monkeypatch.setattr(s, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(s, "SMTP_USERNAME", "u")
    monkeypatch.setattr(s, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(s, "SMTP_FROM_EMAIL", "f@x.example")
    monkeypatch.setattr("app.communications.factory.get_communication_provider", lambda session_factory: _Guarded())
    assert (await reg.execute("crm.acknowledge_enquiry", {"lead_id": lead}, _ctx(tid), skip_approval_gate=True)).sent is True  # once e-mail works, the enquiry still gets its acknowledgement
    assert len(sent) == 1
