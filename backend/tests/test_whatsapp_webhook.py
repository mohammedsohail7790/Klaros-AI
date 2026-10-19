"""The public WhatsApp webhook: Meta's challenge, signature and secret-address checks, filing of replies, de-duplication, consent-gated text, delivery receipts, and secret hygiene.

The router is included in the app by the lead (`app/api/v1/router.py`); this file adds it only if it is not there yet, so it passes either way."""

import hashlib
import hmac
import json
import logging
import time
import uuid

import pytest
from sqlalchemy import select

from app.api.v1 import whatsapp_webhook
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.main import app
from app.models.audit_log import AuditLog
from app.models.communication import CommunicationLog
from app.models.crm import LeadNote
from app.models.notification import Notification
from app.models.whatsapp import WhatsAppInboundMessage
from app.services import whatsapp_service as svc
from tests.test_connector_whatsapp import APP_SECRET, PHONE_ID, TOKEN, connect, lead_phone, meta, open_window  # noqa: F401
from tests.test_consent_gate_intake import _gated
from tests.test_halla_integration import _h, _lead, _register, _tenant_id, halla  # noqa: F401
from tests.test_mt_referrals import _patient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _router_and_limits(monkeypatch):
    if not any(getattr(r, "path", "").startswith("/api/v1/webhooks/whatsapp/") for r in app.routes):
        app.include_router(whatsapp_webhook.router, prefix="/api/v1")
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_ENABLED", False)


def hook(tid) -> str:
    return f"/api/v1/webhooks/whatsapp/{tid}/{svc.url_key(tid)}"


def body(wa_id, text="Hello, is Tuesday free?", *, mid=None, phone_id=PHONE_ID, mtype="text", name="Pat Ient", ts=None, extra=None) -> dict:
    message = {"from": wa_id, "id": mid or f"wamid.{uuid.uuid4().hex[:14]}", "timestamp": str(ts or int(time.time())), "type": mtype}
    message[mtype] = {"body": text} if mtype == "text" else {"caption": text}
    return {"object": "whatsapp_business_account", "entry": [{"id": "222", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "971500000000", "phone_number_id": phone_id},
        "contacts": [{"profile": {"name": name}, "wa_id": wa_id}], "messages": [message], **(extra or {})}}]}]}


def raw(payload) -> bytes:
    return json.dumps(payload).encode()


def sign(data: bytes, secret=APP_SECRET) -> dict:
    return {"x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest(), "content-type": "application/json"}


JSON = {"content-type": "application/json"}


async def notes(client, token, lead):
    return (await client.get(f"/api/v1/leads/{lead}/notes", headers=_h(token))).json()["notes"]


async def rows(tid):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(WhatsAppInboundMessage).where(WhatsAppInboundMessage.tenant_id == tid))).scalars())


# ---- Meta's verification ----------------------------------------------------------------------------------------------------------------------------------

async def test_the_challenge_is_echoed_only_for_this_businesss_verify_token(client) -> None:
    token = await _register(client, "WA Chal Co", "wachal@example.com")
    tid = await _tenant_id(client, token)
    good = {"hub.mode": "subscribe", "hub.verify_token": svc.verify_token(tid), "hub.challenge": "1158201444"}
    r = await client.get(hook(tid), params=good)
    assert r.status_code == 200 and r.text == "1158201444"
    assert (await client.get(hook(tid), params={**good, "hub.verify_token": "wrong"})).status_code == 403
    assert (await client.get(hook(tid), params={**good, "hub.mode": "unsubscribe"})).status_code == 403
    assert (await client.get(hook(tid), params={"hub.mode": "subscribe", "hub.challenge": "1"})).status_code == 403
    other_tid = uuid.uuid4()  # another business's token never works here, and a wrong address key does not exist
    assert (await client.get(hook(tid), params={**good, "hub.verify_token": svc.verify_token(other_tid)})).status_code == 403
    assert (await client.get(f"/api/v1/webhooks/whatsapp/{tid}/not-the-key", params=good)).status_code == 404
    assert (await client.get(f"/api/v1/webhooks/whatsapp/{other_tid}/{svc.url_key(tid)}", params=good)).status_code == 404  # the key is bound to the tenant id


async def test_the_owner_gets_the_two_values_and_they_are_derived_not_secret_material(client) -> None:
    token = await _register(client, "WA Info Co", "wainfo@example.com")
    tid = await _tenant_id(client, token)
    info = (await client.get("/api/v1/integrations/whatsapp/webhook-info", headers=_h(token))).json()
    assert info["callback_url"].endswith(f"/api/v1/webhooks/whatsapp/{tid}/{svc.url_key(tid)}") and info["verify_token"] == svc.verify_token(tid)
    assert info["signature_check"] is False and info["note"] and info["subscribe_to"] == ["messages"]
    assert svc.url_key(tid) != svc.verify_token(tid) and get_settings().JWT_SECRET not in json.dumps(info)
    await connect(tid, app_secret=APP_SECRET)
    again = (await client.get("/api/v1/integrations/whatsapp/webhook-info", headers=_h(token))).json()
    assert again["signature_check"] is True and again["note"] is None and APP_SECRET not in json.dumps(again) and TOKEN not in json.dumps(again)
    assert (await client.get("/api/v1/integrations/whatsapp/webhook-info")).status_code in (401, 403)
    # and the verify token Klaros shows is the one Meta's challenge accepts
    assert (await client.get(hook(tid), params={"hub.mode": "subscribe", "hub.verify_token": info["verify_token"], "hub.challenge": "x"})).status_code == 200


# ---- the POST: trust ---------------------------------------------------------------------------------------------------------------------------------------

async def test_a_signed_call_is_accepted_and_a_missing_or_wrong_signature_is_refused_and_stores_nothing(client) -> None:
    token = await _register(client, "WA Sig Co", "wasig@example.com")
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000222")
    await connect(tid, app_secret=APP_SECRET)
    data = raw(body("971500000222"))
    assert (await client.post(hook(tid), content=data, headers=JSON)).status_code == 403  # no signature
    assert (await client.post(hook(tid), content=data, headers=sign(data, "not-the-secret"))).status_code == 403
    tampered = sign(data)
    assert (await client.post(hook(tid), content=data + b" ", headers=tampered)).status_code == 403  # body changed after signing
    assert await rows(tid) == [] and await notes(client, token, lead) == []
    ok = await client.post(hook(tid), content=data, headers=sign(data))
    assert ok.status_code == 200 and ok.json()["stored"] == 1 and len(await notes(client, token, lead)) == 1


async def test_without_an_app_secret_the_secret_address_is_the_trust_and_a_wrong_key_or_unconnected_business_is_404(client) -> None:
    token = await _register(client, "WA NoSecret Co", "wanosecret@example.com")
    tid = await _tenant_id(client, token)
    await _lead(client, token, phone="+971500000223")
    data = raw(body("971500000223"))
    assert (await client.post(hook(tid), content=data, headers=JSON)).status_code == 404  # not connected: the address does not exist
    await connect(tid)
    assert (await client.post(f"/api/v1/webhooks/whatsapp/{tid}/wrongkey", content=data, headers=JSON)).status_code == 404
    assert (await client.post(hook(tid), content=data, headers=JSON)).status_code == 200
    assert (await client.post(hook(tid), content=b"not json", headers=JSON)).status_code == 422
    from app.integrations.connectors_whatsapp import verify  # noqa: F401  (imported to prove the module loads with the router)
    from app.services.integration_connection_service import IntegrationConnectionService

    await IntegrationConnectionService(async_session_maker).disconnect(tid, "whatsapp")
    assert (await client.post(hook(tid), content=data, headers=JSON)).status_code == 404  # a disconnected business receives nothing


# ---- the POST: filing --------------------------------------------------------------------------------------------------------------------------------------

async def test_a_reply_becomes_a_note_a_notification_an_inbox_row_and_opens_the_window_once(client) -> None:
    token = await _register(client, "WA Reply Co", "wareply@example.com")
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000333")
    await connect(tid)
    payload = body("971500000333", "I can come in March.", mid="wamid.same")
    first = (await client.post(hook(tid), content=raw(payload), headers=JSON)).json()
    assert first["stored"] == 1 and first["duplicate"] == 0
    (note,) = await notes(client, token, lead)
    assert "I can come in March." in note["body"] and "untrusted" in note["body"] and note["author_type"] == "WHATSAPP"
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(Notification).where(Notification.tenant_id == tid, Notification.category == "inbox"))).scalars().first() is not None
        audit = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "inbound_whatsapp.received"))).scalars().all()
    assert len(audit) == 1 and "I can come in March." not in json.dumps(audit[0].input_summary)
    (row,) = await rows(tid)
    assert row.entity_type == "lead" and row.entity_id == uuid.UUID(lead) and row.body_stored and row.message_id == "wamid.same"
    assert await svc.in_window(async_session_maker, tid, "+971500000333")
    again = (await client.post(hook(tid), content=raw(payload), headers=JSON)).json()
    assert again["duplicate"] == 1 and again["stored"] == 0 and len(await notes(client, token, lead)) == 1 and len(await rows(tid)) == 1
    listed = (await client.get("/api/v1/inbox/whatsapp", headers=_h(token))).json()["messages"]
    assert len(listed) == 1 and listed[0]["about"] and listed[0]["body_text"] == "I can come in March."


async def test_a_forged_old_or_future_timestamp_cannot_stretch_the_window_and_media_is_never_downloaded(client) -> None:
    token = await _register(client, "WA Time Co", "watime@example.com")
    tid = await _tenant_id(client, token)
    await _lead(client, token, phone="+971500000444")
    await connect(tid)
    await client.post(hook(tid), content=raw(body("971500000444", ts=int(time.time()) + 10 * 86400)), headers=JSON)  # claims to be from the future
    (row,) = await rows(tid)
    assert (row.received_at.replace(tzinfo=None) - __import__("datetime").datetime.utcnow()).total_seconds() < 5
    await client.post(hook(tid), content=raw(body("971500000444", "my passport", mtype="image")), headers=JSON)
    media = [r for r in await rows(tid) if r.message_type == "image"][0]
    assert "not downloaded" in media.body_text and "my passport" in media.body_text


async def test_an_unknown_number_and_another_phone_number_id_create_nothing_but_a_minimal_record(client) -> None:
    token = await _register(client, "WA Unknown Co", "waunknown@example.com")
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000555")
    await connect(tid)
    r = (await client.post(hook(tid), content=raw(body("971511111111", "who is this")), headers=JSON)).json()
    assert r["unmatched"] == 1
    (row,) = await rows(tid)
    assert row.entity_id is None and row.body_text is None and row.body_stored is False  # no text from a stranger is kept
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(LeadNote).where(LeadNote.tenant_id == tid))).first() is None
        assert (await s.execute(select(Notification).where(Notification.tenant_id == tid, Notification.category == "inbox"))).first() is None
    other_number = (await client.post(hook(tid), content=raw(body("971500000555", phone_id="999")), headers=JSON)).json()
    assert other_number["other_number"] == 1 and len(await rows(tid)) == 1 and await notes(client, token, lead) == []


async def test_a_number_shared_by_two_people_is_never_guessed(client) -> None:
    token = await _register(client, "WA Shared Co", "washared@example.com")
    tid = await _tenant_id(client, token)
    a = await _lead(client, token, name="Person A", phone="+971500000666")
    b = await _lead(client, token, name="Person B", phone="+971500000666")
    await connect(tid)
    r = (await client.post(hook(tid), content=raw(body("971500000666", "private detail")), headers=JSON)).json()
    assert r["ambiguous"] == 1 and await notes(client, token, a) == [] and await notes(client, token, b) == []
    (row,) = await rows(tid)
    assert row.entity_id is None and row.body_text is None


async def test_text_is_withheld_for_a_patient_whose_consent_does_not_cover_it(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "WA Gated Hook Co", "wagatedhook@example.com")
    await connect(tid)
    limited = await _patient(client, token, scopes=["contact", "store_personal_data"])
    full = await _patient(client, token, scopes=["contact", "store_personal_data", "store_medical_information"])
    for lead in (limited, full):
        wa = (await lead_phone(client, token, lead)).lstrip("+")
        r = (await client.post(hook(tid), content=raw(body(wa, "I have diabetes and take insulin.")), headers=JSON)).json()
        assert r["stored"] == 1
    kept, dropped = (await notes(client, token, full))[0]["body"], (await notes(client, token, limited))[0]["body"]
    assert "diabetes" in kept and "diabetes" not in dropped and "consent" in dropped
    by_entity = {r.entity_id: r for r in await rows(tid)}
    assert by_entity[uuid.UUID(limited)].body_text is None and by_entity[uuid.UUID(limited)].body_stored is False
    assert by_entity[uuid.UUID(full)].body_stored is True
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        bodies = " ".join(n.body for n in (await s.execute(select(Notification).where(Notification.tenant_id == tid))).scalars())
    assert "diabetes" not in bodies
    # the window still opens for the limited patient, so an approved template (not text) can follow up
    assert await svc.in_window(async_session_maker, tid, await lead_phone(client, token, limited))


async def test_delivery_receipts_update_the_outbound_log_and_never_go_backwards_or_cross_businesses(client) -> None:
    token = await _register(client, "WA Status Co", "wastatus@example.com")
    tid = await _tenant_id(client, token)
    other_token = await _register(client, "WA Status Other Co", "wastatusother@example.com")
    other_tid = await _tenant_id(client, other_token)
    await connect(tid)
    async with async_session_maker() as s:
        for t in (tid, other_tid):
            await set_tenant_context(s, t)
            s.add(CommunicationLog(tenant_id=t, channel="WHATSAPP", template="lead_follow_up", recipient="971500000777", body="x", status="SENT", provider="whatsapp", external_id="wamid.out1"))
            await s.commit()

    def receipt(state, **extra):
        return body("971500000777", extra={"messages": [], "statuses": [{"id": "wamid.out1", "status": state, "recipient_id": "971500000777", **extra}]})

    async def status(t):
        async with async_session_maker() as s:
            await set_tenant_context(s, t)
            return (await s.execute(select(CommunicationLog.status).where(CommunicationLog.tenant_id == t))).scalar_one()

    for state, expected in (("delivered", "DELIVERED"), ("read", "READ"), ("delivered", "READ")):
        r = (await client.post(hook(tid), content=raw(receipt(state)), headers=JSON)).json()
        assert r["statuses"] == 1 and await status(tid) == expected
    await client.post(hook(tid), content=raw(receipt("failed", errors=[{"code": 131026, "title": "Message undeliverable"}])), headers=JSON)
    assert await status(tid) == "FAILED_WHATSAPP_131026" and await status(other_tid) == "SENT"


async def test_no_secret_appears_in_any_response_or_log(client, caplog) -> None:
    token = await _register(client, "WA Hygiene Co", "wahygiene@example.com")
    tid = await _tenant_id(client, token)
    await _lead(client, token, phone="+971500000888")
    await connect(tid, app_secret=APP_SECRET)
    data = raw(body("971500000888"))
    seen = []
    with caplog.at_level(logging.DEBUG):
        seen.append(await client.post(hook(tid), content=data, headers=JSON))  # refused
        seen.append(await client.post(hook(tid), content=data, headers=sign(data)))
        seen.append(await client.get(hook(tid), params={"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "1"}))
        seen.append(await client.get("/api/v1/integrations/connections", headers=_h(token)))
        seen.append(await client.get("/api/v1/inbox/whatsapp", headers=_h(token)))
    for r in seen:
        assert TOKEN not in r.text and APP_SECRET not in r.text
    assert TOKEN not in caplog.text and APP_SECRET not in caplog.text
