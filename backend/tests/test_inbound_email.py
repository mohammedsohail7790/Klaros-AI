"""Replies to a business's emails come back into Klaros, filed against the right patient or clinic, and nothing else.

What must hold: it is off until configured; each person gets one stable hidden reply address; the webhook answers only to its secret; a reply is stored once; spam and unknown addresses are
dropped; a patient's text is kept only when their consent covers it; attachments are never kept; and one business never sees another's replies."""

import json

import httpx
import pytest
from sqlalchemy import select

from app.communications.base import MessageTemplate
from app.communications.brevo_adapter import BrevoEmailAdapter
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.inbound_email import EmailReplyRoute, InboundEmail
from app.models.notification import Notification
from app.services import inbound_email_service as svc
from tests.test_brevo_email import brevo  # noqa: F401  (the mocked Brevo API)
from tests.test_consent_gate_intake import ALL
from tests.test_halla_integration import _h, _lead, _register, _tenant_id, halla  # noqa: F401
from tests.test_mt_referrals import BASE, _gated, _patient, _provider

pytestmark = pytest.mark.asyncio

DOMAIN, SECRET = "reply.example.test", "test-secret-value-1234567890"
HOOK = f"/api/v1/webhooks/inbound-email/{SECRET}"


@pytest.fixture
def replies_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "INBOUND_REPLY_DOMAIN", DOMAIN)
    monkeypatch.setattr(s, "INBOUND_EMAIL_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(s, "RATE_LIMIT_ENABLED", False)


def _item(token, *, message_id="<m1@mail>", sender="patient@example.com", subject="Re: your enquiry", text="Thanks, I can travel in March.", **over):
    base = {"MessageId": message_id, "From": {"Address": sender, "Name": "Pat Ient"}, "To": [{"Address": f"r-{token}@{DOMAIN}"}], "Subject": subject, "RawTextBody": text,
            "ExtractedMarkdownMessage": text, "Attachments": [], "SpamScore": 0.5}
    return {**base, **over}


async def _lead_with_email(client, token):
    lead = await _lead(client, token, phone="+971500000090")
    j = (await client.get(f"/api/v1/leads/{lead}", headers=_h(token))).json()
    return lead, (j.get("lead") or j)["email"]


async def _token_for(tid, entity_type, entity_id):
    async with async_session_maker() as s:
        return (await s.execute(select(EmailReplyRoute.token).where(EmailReplyRoute.tenant_id == tid, EmailReplyRoute.entity_type == entity_type, EmailReplyRoute.entity_id == entity_id))).scalar_one()


async def test_replies_are_off_until_configured(client) -> None:
    token = await _register(client, "Off Co", "offco@example.com")
    tid = await _tenant_id(client, token)
    _, email = await _lead_with_email(client, token)
    assert await svc.reply_to_for(async_session_maker, tid, email) is None
    assert (await client.post(HOOK, json={"items": []})).status_code == 404  # no secret configured: the address does not exist


async def test_each_person_gets_one_stable_hidden_address_and_strangers_get_none(client, replies_on) -> None:
    token = await _register(client, "Route Co", "routeco@example.com")
    tid = await _tenant_id(client, token)
    lead, email = await _lead_with_email(client, token)
    a = await svc.reply_to_for(async_session_maker, tid, email)
    b = await svc.reply_to_for(async_session_maker, tid, email.upper())
    assert a == b and a.startswith("r-") and a.endswith("@" + DOMAIN) and len(a.split("@")[0]) == 18
    assert await svc.reply_to_for(async_session_maker, tid, "nobody@example.com") is None
    other = await _register(client, "Route Other Co", "routeother@example.com")
    assert await svc.reply_to_for(async_session_maker, await _tenant_id(client, other), email) is None  # another business has no such person


async def test_the_brevo_email_carries_the_reply_to_address(client, brevo, replies_on) -> None:  # noqa: F811
    token = await _register(client, "Header Co", "headerco@example.com")
    tid = await _tenant_id(client, token)
    _, email = await _lead_with_email(client, token)
    assert await BrevoEmailAdapter(async_session_maker).send_email(tid, to=email, subject="Hi", body="Body", template=list(MessageTemplate)[0])
    sent = json.loads(brevo.requests[-1].content)
    assert sent["replyTo"]["email"].endswith("@" + DOMAIN) and sent["replyTo"]["name"] == "Header Co" and sent["sender"]["email"] == "hello@klaros.example"
    # an address nobody owns gets a plain email with no reply-to at all
    await BrevoEmailAdapter(async_session_maker).send_email(tid, to="stranger@example.com", subject="Hi", body="Body", template=list(MessageTemplate)[0])
    assert "replyTo" not in json.loads(brevo.requests[-1].content)


async def test_a_wrong_secret_or_bad_body_is_refused(client, replies_on) -> None:
    assert (await client.post("/api/v1/webhooks/inbound-email/wrong-secret", json={"items": []})).status_code == 404
    assert (await client.post(HOOK, content=b"not json", headers={"content-type": "application/json"})).status_code == 422
    assert (await client.post(HOOK, json={"nope": 1})).json()["received"] == 0


async def test_a_patient_reply_becomes_a_note_a_notification_and_is_stored_once(client, replies_on) -> None:
    token = await _register(client, "Reply Co", "replyco@example.com")
    tid = await _tenant_id(client, token)
    lead, email = await _lead_with_email(client, token)
    reply_to = await svc.reply_to_for(async_session_maker, tid, email)
    t = reply_to.split("@")[0][2:]
    r = (await client.post(HOOK, json={"items": [_item(t, sender=email)]})).json()
    assert r["stored"] == 1
    notes = (await client.get(f"/api/v1/leads/{lead}/notes", headers=_h(token))).json()["notes"]
    assert len(notes) == 1 and "I can travel in March" in notes[0]["body"] and "untrusted" in notes[0]["body"] and notes[0]["author_type"] == "EMAIL"
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(Notification).where(Notification.tenant_id == tid, Notification.category == "inbox"))).scalars().first() is not None
        row = (await s.execute(select(InboundEmail).where(InboundEmail.tenant_id == tid))).scalar_one()
    assert row.sender_matches is True and row.body_stored is True and row.entity_type == "lead"
    again = (await client.post(HOOK, json={"items": [_item(t, sender=email)]})).json()
    assert again["duplicate"] == 1 and again["stored"] == 0
    assert len((await client.get(f"/api/v1/leads/{lead}/notes", headers=_h(token))).json()["notes"]) == 1


async def test_spam_unknown_addresses_attachments_and_bad_messages_are_handled_safely(client, replies_on) -> None:
    token = await _register(client, "Safe Co", "safeco@example.com")
    tid = await _tenant_id(client, token)
    lead, email = await _lead_with_email(client, token)
    t = (await svc.reply_to_for(async_session_maker, tid, email)).split("@")[0][2:]
    items = [
        _item(t, message_id="<spam>", SpamScore=15.0),
        _item("deadbeefdeadbeef", message_id="<unknown>"),
        _item(t, message_id="<nomsgid-sender>", sender="not-an-address"),
        _item(t, message_id="<big>", text="x" * 20000, Attachments=[{"Name": "scan.pdf", "ContentType": "application/pdf", "ContentLength": 1, "DownloadToken": "tok"}]),
        _item(t, message_id="<html>", RawTextBody="", ExtractedMarkdownMessage="", RawHtmlBody="<p>Hello <b>there</b></p><script>alert(1)</script>", subject="Line\r\nBcc: x@y.z"),
    ]
    r = (await client.post(HOOK, json={"items": items})).json()
    assert (r["spam"], r["unknown"], r["invalid"], r["stored"]) == (1, 1, 1, 2)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        rows = {x.message_id: x for x in (await s.execute(select(InboundEmail).where(InboundEmail.tenant_id == tid))).scalars()}
    assert len(rows["<big>"].body_text) == svc.MAX_BODY_CHARS and rows["<big>"].attachment_count == 1  # cut to length; the file itself is never kept
    assert rows["<html>"].body_text == "Hello there" and "\n" not in rows["<html>"].subject and "alert" not in rows["<html>"].body_text


async def test_a_patient_whose_consent_does_not_cover_medical_information_is_recorded_without_their_text(client, halla, replies_on) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gated Reply Co", "gatedreply@example.com")
    limited = await _patient(client, token, scopes=["contact", "store_personal_data"])
    full = await _patient(client, token, scopes=ALL)
    emails = {}
    for lid in (limited, full):
        j = (await client.get(f"/api/v1/leads/{lid}", headers=_h(token))).json()
        emails[lid] = (j.get("lead") or j)["email"]
    for lid in (limited, full):
        t = (await svc.reply_to_for(async_session_maker, tid, emails[lid])).split("@")[0][2:]
        await client.post(HOOK, json={"items": [_item(t, message_id=f"<{lid}>", sender=emails[lid], text="I have diabetes and take insulin.")]})
    kept = (await client.get(f"/api/v1/leads/{full}/notes", headers=_h(token))).json()["notes"][0]["body"]
    dropped = (await client.get(f"/api/v1/leads/{limited}/notes", headers=_h(token))).json()["notes"][0]["body"]
    assert "diabetes" in kept and "diabetes" not in dropped and "consent" in dropped
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        rows = {r.entity_id.hex: r for r in (await s.execute(select(InboundEmail).where(InboundEmail.tenant_id == tid))).scalars()}
    assert any(r.body_text is None and r.body_stored is False for r in rows.values()) and any(r.body_stored for r in rows.values())


async def test_a_clinic_reply_is_listed_flagged_when_from_another_address_and_never_shown_to_another_business(client, halla, replies_on) -> None:  # noqa: F811
    token, tid = await _gated(client, "Clinic Reply Co", "clinicreply@example.com")
    clinic = await _provider(client, token, name="Reply Clinic")
    await client.patch(f"{BASE}/providers/{clinic['id']}/contact", json={"contact_email": "intl@clinic.example"}, headers=_h(token))
    t = (await svc.reply_to_for(async_session_maker, tid, "intl@clinic.example")).split("@")[0][2:]
    await client.post(HOOK, json={"items": [_item(t, message_id="<c1>", sender="someone.else@clinic.example", subject="Re: referral", text="We can see the patient on the 3rd.")]})
    listed = (await client.get("/api/v1/inbox/replies", headers=_h(token))).json()["replies"]
    assert len(listed) == 1 and listed[0]["about"] == "Reply Clinic" and listed[0]["sender_matches"] is False and "the 3rd" in listed[0]["body_text"] and listed[0]["entity_type"] == "provider"
    other = await _register(client, "Clinic Reply Other Co", "clinicreplyother@example.com")
    assert (await client.get("/api/v1/inbox/replies", headers=_h(other))).json()["replies"] == []
