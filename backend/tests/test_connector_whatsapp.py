"""WhatsApp (Meta Cloud API) connector, client and send path, with Meta's HTTP mocked.

What must hold: verification is a real read-only GET that reports honestly and never echoes a secret; sends carry the right payload; a free-form text goes only within 24 hours of the person's last
message (otherwise nothing is sent and the reason says a template is required), a template goes any time; the consent guard decides before anything is sent; the tool is approval-gated and audited."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.api.tool_deps import get_tool_registry
from app.communications.base import MessageTemplate, SendStatus
from app.communications.factory import get_communication_provider
from app.db.session import async_session_maker, set_tenant_context
from app.integrations import connectors_whatsapp, whatsapp_client
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.communication import CommunicationLog
from app.models.rbac import Role
from app.models.whatsapp import WhatsAppInboundMessage
from app.services.integration_connection_service import IntegrationConnectionService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_consent_gate_intake import _gated
from tests.test_halla_integration import _h, _lead, _register, _tenant_id, halla  # noqa: F401
from tests.test_mt_referrals import _patient

pytestmark = pytest.mark.asyncio

PHONE_ID, TOKEN, APP_SECRET = "109876543210987", "EAAGsecretTOKENvalue1234567890abcdef", "app-secret-value-xyz"


class FakeMeta:
    """MOCKED HTTP for graph.facebook.com: records requests; `plan` is a list of responses/exceptions consumed in order, then `default`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.plan: list = []
        self.default = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.plan.pop(0) if self.plan else self.default
        if item is None:
            if request.method == "GET":
                return httpx.Response(200, json={"id": PHONE_ID, "display_phone_number": "+971 50 000 0000", "verified_name": "Aesthetic Tourism", "quality_rating": "GREEN"})
            return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": f"wamid.{uuid.uuid4().hex[:12]}"}]})
        if isinstance(item, Exception):
            raise item
        return item

    def sent(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(whatsapp_client, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))

    async def no_sleep(_):  # the retry back-off must not slow the tests
        return None

    monkeypatch.setattr(whatsapp_client, "_sleep", no_sleep)
    return fake


def _err(status, code, message="boom"):
    return httpx.Response(status, json={"error": {"message": message, "type": "OAuthException", "code": code}})


async def connect(tid, *, app_secret: str | None = None, status_verify=False):
    cred = {"phone_number_id": PHONE_ID, "access_token": TOKEN, "waba_id": "222", **({"app_secret": app_secret} if app_secret else {})}
    return await IntegrationConnectionService(async_session_maker).connect(tid, "whatsapp", cred, created_by=None, verify=status_verify)


async def open_window(tid, phone_digits, *, hours_ago=1.0):
    from app.services.customer_matching import normalize_phone

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        s.add(WhatsAppInboundMessage(tenant_id=tid, message_id=f"wamid.in.{uuid.uuid4().hex[:8]}", wa_id=phone_digits, phone_normalized=normalize_phone(phone_digits), message_type="text",
                                     body_stored=False, received_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago)))
        await s.commit()


async def lead_phone(client, token, lead_id) -> str:
    j = (await client.get(f"/api/v1/leads/{lead_id}", headers=_h(token))).json()
    return (j.get("lead") or j)["phone"]


def _ctx(tid):
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.AI, actor_id=uuid.uuid4(), role=Role.MANAGER)


# ---- the connector and its verification -----------------------------------------------------------------------------------------------------------------

async def test_the_connector_describes_what_the_owner_pastes_and_needs_no_platform_setting() -> None:
    (c,) = connectors_whatsapp.CONNECTORS
    assert (c.key, c.mode, c.category, c.required_settings) == ("whatsapp", "credentials", "communication", ())
    assert [f.name for f in c.fields] == ["phone_number_id", "access_token", "waba_id", "app_secret"]
    assert {f.name: f.secret for f in c.fields} == {"phone_number_id": False, "access_token": True, "waba_id": False, "app_secret": True}
    assert c.verify is not None and c.platform_ready() and len(c.owner_steps) >= 8


async def test_verify_reports_the_number_name_and_quality_from_a_real_read(meta) -> None:
    ok, detail = await connectors_whatsapp.verify({"phone_number_id": PHONE_ID, "access_token": TOKEN})
    assert ok is True and "+971 50 000 0000" in detail and "Aesthetic Tourism" in detail and "GREEN" in detail
    (req,) = meta.requests
    assert req.method == "GET" and req.url.path == f"/v21.0/{PHONE_ID}" and "display_phone_number" in str(req.url.query)
    assert TOKEN not in str(req.url) and req.headers["authorization"] == f"Bearer {TOKEN}"  # the token is only ever in the header
    assert TOKEN not in detail


@pytest.mark.parametrize("response,needle", [(_err(401, 190, f"Invalid OAuth access token {TOKEN}"), "rejected"), (_err(400, 100, "Unsupported get request"), "Could not confirm")])
async def test_verify_is_honest_about_a_refusal_and_never_leaks_the_token(meta, response, needle) -> None:
    meta.default = response
    ok, detail = await connectors_whatsapp.verify({"phone_number_id": PHONE_ID, "access_token": TOKEN, "app_secret": APP_SECRET})
    assert ok is False and needle in detail and TOKEN not in detail and APP_SECRET not in detail


async def test_verify_rejects_missing_or_malformed_input_without_calling_meta(meta) -> None:
    assert (await connectors_whatsapp.verify({"phone_number_id": PHONE_ID}))[0] is False
    ok, detail = await connectors_whatsapp.verify({"phone_number_id": "+971500000000", "access_token": TOKEN})
    assert ok is False and "digits" in detail and meta.requests == []


async def test_verify_survives_a_network_failure_and_an_empty_answer(meta) -> None:
    meta.default = httpx.ConnectError("no route")
    ok, detail = await connectors_whatsapp.verify({"phone_number_id": PHONE_ID, "access_token": TOKEN})
    assert ok is False and "Could not" in detail and len(meta.requests) == whatsapp_client.MAX_ATTEMPTS  # a read is retried, boundedly
    meta.requests.clear()
    meta.default = httpx.Response(200, json={"id": PHONE_ID})
    assert (await connectors_whatsapp.verify({"phone_number_id": PHONE_ID, "access_token": TOKEN}))[0] is False  # 200 with nothing about a number is not "connected"


async def test_the_connection_service_marks_connected_only_after_the_real_call(client, meta) -> None:
    token = await _register(client, "WA Verify Co", "waverify@example.com")
    tid = await _tenant_id(client, token)
    service = IntegrationConnectionService(async_session_maker)
    service.register_verifier("whatsapp", connectors_whatsapp.verify)
    cred = {"phone_number_id": PHONE_ID, "access_token": TOKEN, "app_secret": APP_SECRET}
    assert str((await service.connect(tid, "whatsapp", cred, created_by=None)).status) == "CONNECTED"
    meta.default = _err(401, 190)
    bad = await service.connect(tid, "whatsapp", cred, created_by=None)
    assert str(bad.status) == "ERROR" and TOKEN not in (bad.last_error or "") and APP_SECRET not in (bad.last_error or "")


# ---- the client --------------------------------------------------------------------------------------------------------------------------------------------

async def test_text_and_template_payloads_and_mark_read(meta) -> None:
    mid = await whatsapp_client.send_text(PHONE_ID, TOKEN, to="971500000000", body="Hello")
    assert mid.startswith("wamid.")
    await whatsapp_client.send_template(PHONE_ID, TOKEN, to="971500000000", name="enquiry_followup", language="en_GB", body_params=["Sam", "Tuesday"])
    await whatsapp_client.mark_read(PHONE_ID, TOKEN, message_id="wamid.abc")
    text, template, read = meta.sent()
    assert text == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "971500000000", "type": "text", "text": {"preview_url": False, "body": "Hello"}}
    assert template["type"] == "template" and template["template"]["name"] == "enquiry_followup" and template["template"]["language"] == {"code": "en_GB"}
    assert template["template"]["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Sam"}, {"type": "text", "text": "Tuesday"}]}]
    assert read == {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.abc"}


async def test_a_send_is_retried_only_when_meta_did_not_accept_it(meta) -> None:
    meta.plan = [_err(503, 2), None]
    assert await whatsapp_client.send_text(PHONE_ID, TOKEN, to="971500000000", body="x")
    assert len(meta.requests) == 2  # 503 then success
    meta.requests.clear()
    meta.default = httpx.ReadTimeout("slow")  # it may have been accepted: never send twice
    with pytest.raises(whatsapp_client.WhatsAppNetworkError):
        await whatsapp_client.send_text(PHONE_ID, TOKEN, to="971500000000", body="x")
    assert len(meta.requests) == 1
    meta.requests.clear()
    meta.default = _err(400, 131030, "Recipient phone number not in allowed list")
    with pytest.raises(whatsapp_client.WhatsAppRejectedError) as e:
        await whatsapp_client.send_text(PHONE_ID, TOKEN, to="971500000000", body="x")
    assert len(meta.requests) == 1 and e.value.meta_code == 131030


async def test_errors_never_contain_the_token_or_the_recipient(meta, caplog) -> None:
    meta.default = _err(400, 100, f"Bad token {TOKEN} for 971500000000")
    with caplog.at_level(logging.DEBUG), pytest.raises(whatsapp_client.WhatsAppError) as e:
        await whatsapp_client.send_text(PHONE_ID, TOKEN, to="971500000000", body="x")
    assert TOKEN not in str(e.value) and "[redacted]" in str(e.value)
    assert TOKEN not in caplog.text


async def test_signature_check_is_exact() -> None:
    import hashlib
    import hmac

    body = b'{"entry":[]}'
    good = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert whatsapp_client.verify_signature(body, good, APP_SECRET) is True
    assert whatsapp_client.verify_signature(body + b" ", good, APP_SECRET) is False
    assert whatsapp_client.verify_signature(body, good, "other") is False
    assert not whatsapp_client.verify_signature(body, None, APP_SECRET) and not whatsapp_client.verify_signature(body, "sha256=", APP_SECRET) and not whatsapp_client.verify_signature(body, good, "")


# ---- sending through the consent guard and the 24 hour rule --------------------------------------------------------------------------------------------------

async def _plain(client, name="WA Send Co", email="wasend@example.com"):
    token = await _register(client, name, email)
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000111")
    await connect(tid)
    return token, tid, lead


async def test_a_text_inside_the_24_hour_window_is_sent_and_logged(client, meta) -> None:
    token, tid, lead = await _plain(client)
    await open_window(tid, "971500000111", hours_ago=3)
    r = await get_communication_provider(async_session_maker).deliver_whatsapp(tid, to="+971500000111", body="We can see you Tuesday.", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(lead))
    assert r.status == SendStatus.SENT and r.external_id and r.reason is None
    (payload,) = meta.sent()
    assert payload["type"] == "text" and payload["to"] == "971500000111" and payload["text"]["body"] == "We can see you Tuesday."
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        log = (await s.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tid))).scalar_one()
    assert (log.channel, log.provider, log.status, log.external_id) == ("WHATSAPP", "whatsapp", "SENT", r.external_id) and TOKEN not in log.body


async def test_outside_the_window_without_a_template_nothing_is_sent(client, meta) -> None:
    token, tid, lead = await _plain(client, "WA Late Co", "walate@example.com")
    for hours in (None, 25):  # never wrote, or wrote more than 24 hours ago
        if hours:
            await open_window(tid, "971500000111", hours_ago=hours)
        r = await get_communication_provider(async_session_maker).deliver_whatsapp(tid, to="+971500000111", body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(lead))
        assert r.status == SendStatus.FAILED and r.reason == "outside_24h_window_template_required" and not r.sent
    assert meta.sent() == []
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        statuses = {x.status for x in (await s.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tid))).scalars()}
    assert statuses == {"NOT_SENT_TEMPLATE_REQUIRED"}


async def test_a_template_is_sent_outside_the_window_and_a_template_wins_inside_it(client, meta) -> None:
    token, tid, lead = await _plain(client, "WA Tpl Co", "watpl@example.com")
    provider = get_communication_provider(async_session_maker)
    r = await provider.deliver_whatsapp(tid, to="+971500000111", body="", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(lead), template_name="enquiry_followup", template_params=("Sam",))
    assert r.sent and meta.sent()[0]["type"] == "template" and meta.sent()[0]["template"]["name"] == "enquiry_followup"
    await open_window(tid, "971500000111", hours_ago=1)
    r2 = await provider.deliver_whatsapp(tid, to="+971500000111", body="ignored", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(lead), template_name="enquiry_followup")
    assert r2.sent and meta.sent()[1]["type"] == "template"  # a named template is always sent as a template


async def test_a_meta_refusal_is_reported_as_failed_with_a_code_and_not_connected_is_honest(client, meta) -> None:
    token, tid, lead = await _plain(client, "WA Fail Co", "wafail@example.com")
    meta.default = _err(400, 132001, "Template name does not exist in the translation")
    r = await get_communication_provider(async_session_maker).deliver_whatsapp(tid, to="+971500000111", body="", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(lead), template_name="nope")
    assert r.status == SendStatus.FAILED and r.reason == "whatsapp_rejected_132001"
    other = await _register(client, "WA None Co", "wanone@example.com")
    other_tid = await _tenant_id(client, other)
    r2 = await get_communication_provider(async_session_maker).deliver_whatsapp(other_tid, to="+971500000111", body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP)
    assert r2.status == SendStatus.FAILED and r2.reason == "whatsapp_not_connected"


async def test_a_disconnected_business_cannot_send(client, meta) -> None:
    token, tid, lead = await _plain(client, "WA Off Co", "waoff@example.com")
    await open_window(tid, "971500000111")
    await IntegrationConnectionService(async_session_maker).disconnect(tid, "whatsapp")
    r = await get_communication_provider(async_session_maker).deliver_whatsapp(tid, to="+971500000111", body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(lead))
    assert r.reason == "whatsapp_not_connected" and meta.sent() == []


async def test_the_consent_guard_refuses_before_anything_reaches_whatsapp(client, halla, meta) -> None:  # noqa: F811
    token, tid = await _gated(client, "WA Gated Co", "wagated@example.com")
    await connect(tid)
    no_contact = await _patient(client, token, scopes=["store_personal_data", "store_medical_information"])
    contact = await _patient(client, token, scopes=["contact", "store_personal_data", "store_medical_information"])
    provider = get_communication_provider(async_session_maker)
    for lead in (no_contact, contact):
        await open_window(tid, (await lead_phone(client, token, lead)).lstrip("+"))
    refused = await provider.deliver_whatsapp(tid, to=await lead_phone(client, token, no_contact), body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(no_contact))
    assert refused.status == SendStatus.BLOCKED_CONSENT and refused.reason == "no_contact_consent" and meta.sent() == []
    stranger = await provider.deliver_whatsapp(tid, to="+971599999999", body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP)
    assert stranger.status == SendStatus.BLOCKED_CONSENT and meta.sent() == []  # a number that is nobody's record is never messaged
    wrong_person = await provider.deliver_whatsapp(tid, to=await lead_phone(client, token, no_contact), body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(contact))
    assert wrong_person.blocked and meta.sent() == []  # consent of one patient cannot authorise a message about another
    ok = await provider.deliver_whatsapp(tid, to=await lead_phone(client, token, contact), body="Hi", template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=uuid.UUID(contact))
    assert ok.sent and len(meta.sent()) == 1


# ---- the governed tool --------------------------------------------------------------------------------------------------------------------------------------

async def test_the_tool_waits_for_approval_by_default_and_never_takes_a_phone_number(client, meta) -> None:
    assert DEFAULT_TOOL_POLICIES["communications.send_whatsapp"] == ActionPolicy.APPROVAL_REQUIRED
    token, tid, lead = await _plain(client, "WA Tool Co", "watool@example.com")
    reg = get_tool_registry()
    with pytest.raises(ToolApprovalRequiredError):
        await reg.execute("communications.send_whatsapp", {"lead_id": lead, "message": "Hi"}, _ctx(tid))
    assert meta.sent() == []
    assert "phone" not in reg.get("communications.send_whatsapp").input_schema.model_fields


async def test_the_tool_sends_in_window_refuses_outside_it_and_audits_each_attempt(client, meta) -> None:
    token, tid, lead = await _plain(client, "WA Tool2 Co", "watool2@example.com")
    reg = get_tool_registry()
    late = await reg.execute("communications.send_whatsapp", {"lead_id": lead, "message": "Hi"}, _ctx(tid), skip_approval_gate=True)
    assert late.sent is False and late.reason == "outside_24h_window_template_required" and meta.sent() == []
    tpl = await reg.execute("communications.send_whatsapp", {"lead_id": lead, "template_name": "enquiry_followup", "template_params": ["Sam"]}, _ctx(tid), skip_approval_gate=True)
    assert tpl.sent is True and tpl.mode == "template" and tpl.message_id
    await open_window(tid, "971500000111")
    txt = await reg.execute("communications.send_whatsapp", {"lead_id": lead, "message": "See you Tuesday"}, _ctx(tid), skip_approval_gate=True)
    assert txt.sent is True and txt.mode == "text"
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        rows = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action.like("communications.whatsapp_%")).order_by(AuditLog.created_at))).scalars().all()
    assert [r.action for r in rows] == ["communications.whatsapp_not_sent", "communications.whatsapp_sent", "communications.whatsapp_sent"]
    assert rows[0].result == "failure" and rows[0].input_summary == {"mode": "text", "reason": "outside_24h_window_template_required"}
    assert "See you Tuesday" not in json.dumps([r.input_summary for r in rows]) and "971500000111" not in json.dumps([r.input_summary for r in rows])


async def test_the_tool_reports_consent_refusal_missing_person_and_missing_number(client, halla, meta) -> None:  # noqa: F811
    token, tid = await _gated(client, "WA Tool Gated Co", "watoolgated@example.com")
    await connect(tid)
    no_contact = await _patient(client, token, scopes=["store_personal_data"])
    reg = get_tool_registry()
    out = await reg.execute("communications.send_whatsapp", {"lead_id": no_contact, "template_name": "t"}, _ctx(tid), skip_approval_gate=True)
    assert out.sent is False and out.reason == "no_contact_consent" and meta.sent() == []
    gone = await reg.execute("communications.send_whatsapp", {"lead_id": str(uuid.uuid4()), "message": "Hi"}, _ctx(tid), skip_approval_gate=True)
    assert gone.sent is False and gone.reason == "person_not_found"
    with pytest.raises(Exception):
        await reg.execute("communications.send_whatsapp", {"message": "Hi"}, _ctx(tid), skip_approval_gate=True)  # no target
