"""Second tier of the consent gate: every OTHER way personal data enters a consent-gated tenant (customers, notes, outbound lists, inbound Twilio SMS/voice,
marketplace leads, referrals, lead->customer conversion) and the single choke point for OUTBOUND messages. Non-gated tenants are checked unchanged.
SQLite + in-process HTTP (not a deployed service)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.communications.base import CommunicationProvider, MessageTemplate
from app.communications.consent_guard import ConsentGuardedProvider
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Customer, Lead
from tests.test_business_journey_api import _register
from tests.test_consent_gate_intake import ALL, HEALTH, PII_EMAIL, PII_NAME, PII_PHONE, _body, _enable_vertical, _evidence, _gated, _leads, _plain
from tests.test_halla_integration import _h, _tenant_id, halla  # noqa: F401


class Recorder(CommunicationProvider):
    provider_name = "recorder"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_email(self, tenant_id, *, to, subject, body, template) -> bool:
        self.sent.append(("email", to))
        return True

    async def send_sms(self, tenant_id, *, to, body, template) -> bool:
        self.sent.append(("sms", to))
        return True


async def _attested_lead(client, token, **over):
    r = await client.post("/api/v1/leads", json=_body(consent={"scopes": ALL}, **over), headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["lead"]["id"]


# ----------------------------------------------------------------------------------------------- outbound: the single choke point
def test_the_communication_factory_always_returns_the_consent_guard() -> None:
    from app.communications.factory import get_communication_provider

    assert isinstance(get_communication_provider(async_session_maker), ConsentGuardedProvider)


async def test_outbound_messages_need_contact_consent_for_the_recipient(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Out", "gateout@example.com")
    inner = Recorder()
    guard = ConsentGuardedProvider(inner, async_session_maker)
    lead = await _attested_lead(client, token)
    send = lambda **kw: guard.send_email(tid, subject="s", body="b", template=kw.pop("template", MessageTemplate.LEAD_FOLLOW_UP), **kw)  # noqa: E731

    assert await send(to=PII_EMAIL) is True and await guard.send_sms(tid, to=PII_PHONE, body="b", template=MessageTemplate.LEAD_FOLLOW_UP) is True
    assert await send(to="stranger@example.com") is False                                         # a recipient that matches nobody is not messaged
    # the TEAM_INVITE template name alone exempts nothing (see test_mt_hardening_comms.py for the genuine pending-invitee path)
    assert await send(to="new.staff@example.com", template=MessageTemplate.TEAM_INVITE) is False

    # reaching the lead through a customer record that links to it
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        cust = Customer(tenant_id=tid, name="Via Customer", email="via.customer@example.com", status="ACTIVE")
        s.add(cust)
        await s.flush()
        (await s.get(Lead, uuid.UUID(lead))).customer_id = cust.id
        await s.commit()
    assert await send(to="via.customer@example.com") is True

    # partial withdrawal of contact -> nothing more is sent to any of the lead's addresses
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["store_personal_data"]}, headers=_h(token))
    before = len(inner.sent)
    assert await send(to=PII_EMAIL) is False and await guard.send_sms(tid, to=PII_PHONE, body="b", template=MessageTemplate.NURTURE_MESSAGE) is False
    assert await send(to="via.customer@example.com") is False
    assert len(inner.sent) == before
    # a re-grant restores it (newest evidence wins)
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["contact", "store_personal_data"]}, headers=_h(token))
    assert await send(to=PII_EMAIL) is True


async def test_outbound_messages_are_unchanged_for_other_tenants(client, halla) -> None:  # noqa: F811
    token, _ = await _plain(client, "Plain Out", "plainout@example.com")
    tid = await _tenant_id(client, token)
    inner = Recorder()
    guard = ConsentGuardedProvider(inner, async_session_maker)
    assert await guard.send_email(tid, to="anyone@example.com", subject="s", body="b", template=MessageTemplate.INVOICE_SENT) is True
    assert await guard.send_sms(tid, to="+15550000000", body="b", template=MessageTemplate.COLLECTION_REMINDER) is True
    assert len(inner.sent) == 2


# ----------------------------------------------------------------------------------------------- customers, notes, outbound lists
async def test_customers_cannot_be_created_or_imported_directly_and_lists_cannot_be_built_for_a_gated_tenant(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Cust", "gatecust@example.com")
    r = await client.post("/api/v1/customers", json={"name": PII_NAME, "email": PII_EMAIL}, headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "direct_customer_creation_disabled_for_consent_gated_tenant"
    r = await client.post("/api/v1/customers/import", json=[{"name": PII_NAME, "email": PII_EMAIL}], headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "bulk_import_disabled_without_per_person_consent"
    r = await client.post("/api/v1/marketing/outbound/contacts", json={"list_id": str(uuid.uuid4()), "contact_name": PII_NAME, "email": PII_EMAIL}, headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "outbound_list_building_disabled_for_consent_gated_tenant"
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(Customer).where(Customer.tenant_id == tid))).scalars().all() == []
    from app.models.audit_log import AuditLog

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert PII_NAME not in " ".join(str(a.input_summary) for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars())


async def test_customers_still_work_for_other_tenants(client, halla) -> None:  # noqa: F811
    token, _ = await _plain(client, "Plain Cust", "plaincust@example.com")
    assert (await client.post("/api/v1/customers", json={"name": "Plain Customer", "email": "pc@example.com"}, headers=_h(token))).status_code == 201
    assert (await client.post("/api/v1/customers/import", json=[{"name": "Two", "email": "two@example.com"}], headers=_h(token))).status_code == 201


async def test_a_customer_note_needs_the_medical_scope_on_every_linked_lead(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Note", "gatenote@example.com")
    personal_only = (await client.post("/api/v1/leads", json=_body(consent={"scopes": ["store_personal_data"]}), headers=_h(token))).json()["lead"]["id"]
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        cust = Customer(tenant_id=tid, name="Linked", status="ACTIVE")
        orphan = Customer(tenant_id=tid, name="No Lead", status="ACTIVE")
        s.add_all([cust, orphan])
        await s.flush()
        (await s.get(Lead, uuid.UUID(personal_only))).customer_id = cust.id
        await s.commit()
        cid, oid = cust.id, orphan.id
    note = lambda c: client.post(f"/api/v1/customers/{c}/notes", json={"body": HEALTH}, headers=_h(token))  # noqa: E731
    assert (await note(cid)).status_code == 422 and (await note(oid)).status_code == 422       # no medical scope / no lead behind the customer
    await client.post(f"/api/v1/leads/{personal_only}/consent", json={"scopes": ["store_personal_data", "store_medical_information"]}, headers=_h(token))
    assert (await note(cid)).status_code == 201
    from app.models.audit_log import AuditLog

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert HEALTH not in " ".join(str(a.input_summary) for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars())


# ----------------------------------------------------------------------------------------------- inbound Twilio, marketplace
_TOKEN = "test_twilio_auth_token"


def _twilio_sign(url: str, params: dict) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(_TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


@pytest.mark.parametrize("channel,params", [("inbound-sms", {"MessageSid": "SMgate1", "From": "+15551230001", "Body": "ZXQ health words"}), ("inbound-voice", {"CallSid": "CAgate1", "From": "+15551230002"})])
async def test_inbound_twilio_to_a_gated_tenant_stores_nothing_and_promises_nothing(client, halla, monkeypatch, channel, params) -> None:  # noqa: F811
    from app.models.integration import WebhookEvent

    monkeypatch.setattr(get_settings(), "TWILIO_AUTH_TOKEN", _TOKEN)
    token, tid = await _gated(client, f"Gate Tw {channel}", f"gatetw{channel[-3:]}@example.com")
    path = f"/api/v1/webhooks/twilio/{channel}/{tid}"
    r = await client.post(path, data=params, headers={"X-Twilio-Signature": _twilio_sign("http://test" + path, params)})
    assert r.status_code == 200 and r.text == "<Response></Response>"
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(Lead).where(Lead.tenant_id == tid))).scalars().all() == []
        assert (await s.execute(select(WebhookEvent).where(WebhookEvent.tenant_id == tid))).scalars().all() == []   # not even the raw payload
    bad = await client.post(path, data=params, headers={"X-Twilio-Signature": "bogus"})
    assert bad.status_code == 400                                                                                       # signature checks are unchanged


async def test_a_marketplace_lead_for_a_gated_tenant_is_acknowledged_but_not_stored(client, halla) -> None:  # noqa: F811
    from app.models.integration import WebhookEvent

    token, tid = await _gated(client, "Gate Mkt", "gatemkt@example.com")
    secret = "mkt-secret"
    r = await client.post("/api/v1/integrations/connections/angi/connect", json={"credential": {"webhook_secret": secret}}, headers=_h(token))
    assert r.status_code == 200, r.text
    body = json.dumps({"id": "angi-1", "name": PII_NAME, "phone": "555-111-2222", "email": PII_EMAIL, "service": "x", "message": HEALTH}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = await client.post(f"/api/v1/webhooks/marketplace/angi/{tid}", content=body, headers={"X-Klaros-Signature": sig})
    assert resp.status_code == 200 and resp.json() == {"received": True, "deduplicated": False, "lead_id": None, "refused": "consent_required"}
    assert await _leads(client, token) == []
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert [w for w in (await s.execute(select(WebhookEvent).where(WebhookEvent.tenant_id == tid))).scalars() if "marketplace" in w.provider] == []


# ----------------------------------------------------------------------------------------------- referrals, conversion
async def test_a_referral_cannot_turn_a_third_party_into_a_lead_for_a_gated_tenant(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Ref", "gateref@example.com")
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        cust = Customer(tenant_id=tid, name="Referrer", status="ACTIVE")
        s.add(cust)
        await s.commit()
        cid = str(cust.id)
    R = "/api/v1/retention/referrals"
    prog = (await client.post(f"{R}/programs", json={"name": "Refer", "reward_type": "credit", "reward_amount": "10.00"}, headers=_h(token))).json()
    code = (await client.post(f"{R}/codes", json={"program_id": prog["program_id"], "customer_id": cid}, headers=_h(token))).json()
    ref = (await client.post(R, json={"referral_code_id": code["code_id"]}, headers=_h(token))).json()
    r = await client.post(f"{R}/{ref['referral_id']}/convert-to-lead", json={"name": PII_NAME, "phone": PII_PHONE}, headers=_h(token))
    assert r.status_code == 422 and r.json()["detail"]["missing_scopes"] == ["store_personal_data"]
    assert await _leads(client, token) == []


async def test_converting_a_lead_to_a_customer_needs_personal_data_consent_for_a_gated_tenant(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gate Conv", "gateconv@example.com")
    consented = await _attested_lead(client, token)
    async with async_session_maker() as s:                                            # a legacy lead with no evidence at all
        await set_tenant_context(s, tid)
        legacy = Lead(tenant_id=tid, name="Legacy", source="WEB", status="NEW", qualification_status="PENDING", urgency="MEDIUM", email="legacy@example.com")
        s.add(legacy)
        await s.commit()
        legacy_id = str(legacy.id)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    conv = lambda lead: client.post("/api/v1/jobs/convert-lead", json={"lead_id": lead, "title": "Consult", "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat()}, headers=_h(token))  # noqa: E731
    r = await conv(legacy_id)
    assert r.status_code == 422 and r.json()["detail"]["missing_scopes"] == ["store_personal_data"]
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(Customer).where(Customer.tenant_id == tid))).scalars().all() == []
    assert (await conv(consented)).status_code == 201


async def test_a_vertical_only_tenant_is_gated_on_every_second_tier_path(client, halla) -> None:  # noqa: F811
    token = await _register(client, "Vert Chan", "vertchan@example.com")
    tid = await _tenant_id(client, token)
    await _enable_vertical(tid)
    assert (await client.post("/api/v1/customers", json={"name": "X"}, headers=_h(token))).status_code == 422
    assert (await client.post("/api/v1/marketing/outbound/contacts", json={"list_id": str(uuid.uuid4()), "email": "a@b.co"}, headers=_h(token))).status_code == 422
    inner = Recorder()
    assert await ConsentGuardedProvider(inner, async_session_maker).send_email(tid, to="a@b.co", subject="s", body="b", template=MessageTemplate.LEAD_FOLLOW_UP) is False
    assert inner.sent == [] and await _evidence(tid) == []
