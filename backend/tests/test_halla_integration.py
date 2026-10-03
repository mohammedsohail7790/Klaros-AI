"""Klaros <-> Halla integration (Klaros side).

LABELLING — read this before trusting a green run:
  * Halla itself is NOT contacted. Every outbound request here goes to an `httpx.MockTransport` that records it
    and answers as the contract describes ("MOCKED HTTP"). These tests prove what Klaros SENDS and how it reacts,
    not that a real Halla accepts it.
  * Inbound webhooks are signed in the test with the contract's algorithm (HMAC-SHA256 over timestamp + "." + raw
    body) and delivered to the real Klaros endpoint, the real signature check, the real database and the real
    event bus ("real Klaros code, test-signed payloads").
  * The database is SQLite here; PostgreSQL/RLS evidence lives in the PostgreSQL-gated run (see the report).
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

pytestmark = pytest.mark.asyncio

from app.integrations.credential_store import decrypt_credential  # noqa: E402
from app.integrations.workforce import halla_webhook as hw  # noqa: E402
from app.integrations.workforce.contract import WorkforceUnavailableError  # noqa: E402
from app.integrations.workforce.halla_client import (  # noqa: E402
    HallaClient,
    HallaConfigError,
    HallaEndpoint,
    _segment,
    validate_base_url,
)
from app.models.event import EventType  # noqa: E402
from tests.test_business_builder_api import _seed_registry  # noqa: E402
from tests.test_business_journey_api import _register, _tech_token  # noqa: E402

BASE = "/api/v1/business-builder"
API_KEY = "halla-test-key-0123456789"
SECRET = "whsec-test-secret-0123456789"
HALLA_TENANT = "halla-tenant-aaaa"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


# --------------------------------------------------------------------------- fixtures


class FakeHalla:
    """MOCKED HTTP: records requests and answers per route; `overrides` lets a test make a route fail."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.overrides: dict[tuple[str, str], httpx.Response | Exception] = {}
        self.tenant_in_health: str | None = HALLA_TENANT

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key in self.overrides:
            r = self.overrides[key]
            if isinstance(r, Exception):
                raise r
            return r
        p = request.url.path
        if p.endswith("/integrations/klaros/health"):
            return httpx.Response(200, json={"status": "ok", **({"tenant_id": self.tenant_in_health} if self.tenant_in_health else {})})
        if p.endswith("/integrations/klaros/workforce"):
            return httpx.Response(200, json={"data": {"agentName": "Aria", "servicesOffered": ["x"]}})
        if p.endswith("/integrations/klaros/agents"):
            return httpx.Response(200, json={"agents": [{"id": "ag1", "name": "Receptionist", "role": "inbound", "active": True}, {"id": "ag2", "name": "Follow-up", "status": "paused"}, {"nope": 1}]})
        if p.endswith("/api/v1/leads") and request.method == "POST":
            return httpx.Response(201, json={"success": True, "data": {"id": "halla-lead-1"}})
        if "/api/v1/leads/" in p and request.method == "PUT":
            return httpx.Response(200, json={"success": True})
        if p.endswith("/calls/outbound"):
            return httpx.Response(200, json={"success": True, "callSid": "CA123"})
        return httpx.Response(404)

    def sent(self, method: str, suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(suffix)]


@pytest.fixture
def halla(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.workforce import halla_adapter, registry

    s = get_settings()
    saved = {k: getattr(s, k) for k in ("WORKFORCE_ADAPTER", "HALLA_API_BASE_URL", "HALLA_API_KEY_HEADER", "HALLA_API_KEY_SCHEME", "HALLA_ALLOWED_HOSTS", "KLAROS_PUBLIC_API_URL", "ENV")}
    s.WORKFORCE_ADAPTER, s.HALLA_API_BASE_URL = "halla", "https://halla.test"
    s.HALLA_API_KEY_HEADER, s.HALLA_API_KEY_SCHEME, s.HALLA_ALLOWED_HOSTS = "X-Test-Key", None, None
    s.KLAROS_PUBLIC_API_URL = "https://klaros.test"
    fake = FakeHalla()
    transport = httpx.MockTransport(fake)

    async def no_sleep(_):  # retries must not slow the suite
        return None

    original = HallaClient

    def factory(endpoint, api_key, **kw):
        kw.setdefault("transport", transport)
        kw["sleep"] = no_sleep
        return original(endpoint, api_key, **kw)

    monkeypatch.setattr(halla_adapter, "HallaClient", factory)
    registry._halla_instance = None
    yield fake
    registry._halla_instance = None
    for k, v in saved.items():
        setattr(s, k, v)


async def _connect(client, token, *, halla_tenant=HALLA_TENANT, api_key=API_KEY, secret=SECRET):
    return await client.put(f"{BASE}/workforce/halla/connection", json={"halla_tenant_id": halla_tenant, "api_key": api_key, "signing_secret": secret}, headers=_h(token))


async def _tenant_id(client, token) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/users/me", headers=_h(token))).json()["tenant_id"])


async def _lead(client, token, name="Pat Ient", phone="+15550001111"):
    body = {"name": name, "source": "WEB", "email": f"{uuid.uuid4().hex[:6]}@example.com"}
    if phone:
        body["phone"] = phone
    r = await client.post("/api/v1/leads", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return (r.json().get("lead") or r.json())["id"]


def _body(etype, tenant=HALLA_TENANT, data=None, eid=None, at=None):
    return json.dumps({"id": eid or f"evt-{uuid.uuid4().hex[:10]}", "type": etype, "timestamp": (at or datetime.now(timezone.utc)).isoformat(), "tenant_id": tenant, "data": data or {}}).encode()


async def _deliver(client, tenant_id, raw: bytes, *, secret=SECRET, ts=None, sig=None):
    ts = ts or str(int(time.time()))
    headers = {"X-HallaAI-Timestamp": ts, "X-HallaAI-Signature": sig if sig is not None else hw.sign(secret, ts, raw), "Content-Type": "application/json"}
    return await client.post(f"/api/v1/webhooks/halla/{tenant_id}", content=raw, headers=headers)


# ------------------------------------------------------------------ client (MOCKED HTTP)


def _client(handler, **kw):
    async def no_sleep(_):
        return None

    return HallaClient(HallaEndpoint("https://halla.test", "X-Test-Key", None), API_KEY, transport=httpx.MockTransport(handler), sleep=no_sleep, **kw)


async def test_client_uses_the_exact_contract_routes_and_the_tenant_credential_header() -> None:
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    c = _client(handler)
    await c.health(); await c.get_workforce(); await c.put_workforce({"a": 1}); await c.list_agents()
    await c.create_lead({"klarosLeadId": "k"}); await c.update_lead("h1", {"klarosLeadId": "k"}); await c.outbound_call({"toNumber": "+1"})
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/integrations/klaros/health"), ("GET", "/api/v1/integrations/klaros/workforce"),
        ("PUT", "/api/v1/integrations/klaros/workforce"), ("GET", "/api/v1/integrations/klaros/agents"),
        ("POST", "/api/v1/leads"), ("PUT", "/api/v1/leads/h1"), ("POST", "/api/v1/calls/outbound"),
    ]
    assert all(r.headers["X-Test-Key"] == API_KEY for r in seen)
    assert json.loads(seen[2].content) == {"a": 1}


async def test_client_scheme_prefix_and_no_redirect_following() -> None:
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(302, headers={"location": "https://evil.test/"})

    c = HallaClient(HallaEndpoint("https://halla.test", "Authorization", "Bearer"), API_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(WorkforceUnavailableError) as e:
        await c.health()
    assert seen[0].headers["Authorization"] == f"Bearer {API_KEY}"
    assert len(seen) == 1 and "redirect" in str(e.value)  # never followed


async def test_client_retries_only_idempotent_requests_and_only_retryable_failures() -> None:
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})

    assert (await _client(flaky, max_retries=2).health()) == {"ok": 1} and calls["n"] == 3

    calls["n"] = 0
    with pytest.raises(WorkforceUnavailableError) as e:
        await _client(lambda r: (calls.__setitem__("n", calls["n"] + 1), httpx.Response(503))[1], max_retries=2).health()
    assert calls["n"] == 3 and e.value.retryable  # bounded: 1 + 2 retries

    calls["n"] = 0
    with pytest.raises(WorkforceUnavailableError):
        await _client(lambda r: (calls.__setitem__("n", calls["n"] + 1), httpx.Response(503))[1], max_retries=2).create_lead({})
    assert calls["n"] == 1  # POST is never replayed

    calls["n"] = 0
    with pytest.raises(WorkforceUnavailableError) as e:
        await _client(lambda r: (calls.__setitem__("n", calls["n"] + 1), httpx.Response(401, text="secret body"))[1], max_retries=2).health()
    assert calls["n"] == 1 and not e.value.retryable and e.value.status_code == 401  # a 4xx is not retried
    assert "secret body" not in str(e.value) and API_KEY not in str(e.value)


@pytest.mark.parametrize("status,retryable", [(401, False), (403, False), (404, False), (409, False), (429, True), (500, True), (502, True)])
async def test_client_maps_every_error_status(status, retryable) -> None:
    with pytest.raises(WorkforceUnavailableError) as e:
        await _client(lambda r: httpx.Response(status), max_retries=0).get_workforce()
    assert e.value.status_code == status and e.value.retryable is retryable


async def test_client_timeout_and_connection_errors_and_malformed_body() -> None:
    def boom(req):
        raise httpx.ConnectTimeout("t")

    with pytest.raises(WorkforceUnavailableError) as e:
        await _client(boom, max_retries=1).health()
    assert e.value.retryable
    with pytest.raises(WorkforceUnavailableError) as e:
        await _client(lambda r: (_ for _ in ()).throw(httpx.ConnectError("c")), max_retries=0).health()
    assert "could not be reached" in str(e.value)
    with pytest.raises(WorkforceUnavailableError) as e:
        await _client(lambda r: httpx.Response(200, content=b"<html>"), max_retries=0).health()
    assert "malformed" in str(e.value)
    assert await _client(lambda r: httpx.Response(200, json=[1, 2]), max_retries=0).health() == {"data": [1, 2]}


def test_base_url_is_validated_and_never_tenant_input() -> None:
    assert validate_base_url("https://gateway.hallaai.com/", allowed_hosts=None, production=True) == "https://gateway.hallaai.com"
    for bad in ("http://gateway.hallaai.com", "ftp://x", "https://user:pw@h.com", "https://h.com?x=1", "", "not a url"):
        with pytest.raises(HallaConfigError):
            validate_base_url(bad, allowed_hosts=None, production=True)
    with pytest.raises(HallaConfigError):
        validate_base_url("https://other.com", allowed_hosts="gateway.hallaai.com", production=False)
    assert validate_base_url("http://localhost:4000", allowed_hosts=None, production=False) == "http://localhost:4000"
    with pytest.raises(HallaConfigError):
        validate_base_url("http://localhost:4000", allowed_hosts=None, production=True)  # plain http never in production
    with pytest.raises(WorkforceUnavailableError):
        _segment("../../admin")
    assert _segment("abc-123") == "abc-123"


# ------------------------------------------------------------ webhook crypto (pure)


def test_signature_accepts_a_valid_request_in_every_documented_encoding() -> None:
    raw = b'{"id":"1"}'
    now = datetime.now(timezone.utc)
    for ts in (str(int(now.timestamp())), str(int(now.timestamp() * 1000)), now.isoformat()):
        sig = hw.sign(SECRET, ts, raw)
        hw.verify_signature(raw, ts, sig, SECRET, tolerance_seconds=300)
        hw.verify_signature(raw, ts, "sha256=" + sig.upper(), SECRET, tolerance_seconds=300)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda raw, ts, sig: (raw + b" ", ts, sig), "signature_invalid"),  # modified body
        (lambda raw, ts, sig: (raw, ts, "0" * 64), "signature_invalid"),
        (lambda raw, ts, sig: (raw, ts, ""), "signature_missing"),
        (lambda raw, ts, sig: (raw, ts, "zz"), "signature_malformed"),
        (lambda raw, ts, sig: (raw, "", sig), "timestamp_missing"),
        (lambda raw, ts, sig: (raw, "not-a-time", sig), "timestamp_malformed"),
        (lambda raw, ts, sig: (raw, str(int(time.time()) - 3600), hw.sign(SECRET, str(int(time.time()) - 3600), raw)), "timestamp_stale"),
        (lambda raw, ts, sig: (raw, str(int(time.time()) + 3600), hw.sign(SECRET, str(int(time.time()) + 3600), raw)), "timestamp_stale"),
    ],
)
def test_signature_rejects_everything_that_is_not_authentic_and_fresh(mutate, reason) -> None:
    raw, ts = b'{"id":"1"}', str(int(time.time()))
    raw2, ts2, sig2 = mutate(raw, ts, hw.sign(SECRET, ts, raw))
    with pytest.raises(hw.HallaWebhookError) as e:
        hw.verify_signature(raw2, ts2, sig2, SECRET, tolerance_seconds=300)
    assert e.value.reason == reason and e.value.status == 401


def test_signature_binds_the_timestamp_and_the_secret() -> None:
    raw, ts = b"{}", str(int(time.time()))
    sig = hw.sign(SECRET, ts, raw)
    with pytest.raises(hw.HallaWebhookError):
        hw.verify_signature(raw, str(int(ts) + 1), sig, SECRET, tolerance_seconds=300)  # replaying a signature with a new time
    with pytest.raises(hw.HallaWebhookError):
        hw.verify_signature(raw, ts, sig, "another-secret", tolerance_seconds=300)
    with pytest.raises(hw.HallaWebhookError) as e:
        hw.verify_signature(raw, ts, sig, "", tolerance_seconds=300)
    assert e.value.status == 503


def test_envelope_parsing_and_the_event_vocabulary() -> None:
    assert hw.SUPPORTED_EVENT_TYPES == {
        "call.started", "call.completed", "lead.created", "lead.updated", "lead.qualified", "lead.escalated",
        "appointment.confirmed", "appointment.rescheduled", "appointment.cancelled",
    }
    assert "appointment.requested" not in hw.SUPPORTED_EVENT_TYPES  # Halla never emits it
    ok = hw.parse_envelope(_body("lead.qualified", data={"qualification": "qualified"}))
    assert ok.type == "lead.qualified" and ok.tenant_id == HALLA_TENANT
    for raw, reason in [
        (b"not json", "body_not_json"), (b"[]", "envelope_not_object"),
        (json.dumps({"type": "call.started", "tenant_id": "t"}).encode(), "envelope_id_invalid"),
        (json.dumps({"id": "1", "tenant_id": "t"}).encode(), "envelope_type_invalid"),
        (json.dumps({"id": "1", "type": "call.started"}).encode(), "envelope_tenant_invalid"),
        (json.dumps({"id": "1", "type": "call.started", "tenant_id": "t", "data": []}).encode(), "envelope_data_invalid"),
        (_body("appointment.requested"), "event_type_unsupported"), (_body("something.else"), "event_type_unsupported"),
    ]:
        with pytest.raises(hw.HallaWebhookError) as e:
            hw.parse_envelope(raw)
        assert e.value.reason == reason


def test_escalation_is_read_from_flat_flags_and_the_embedded_escalation_object() -> None:
    assert hw.escalated({"escalated": True}) and hw.escalated({"needsHuman": "true"})
    assert hw.escalated({"escalation": {"transferred": True, "target": "sales"}}) and hw.escalated({"escalation": {"reason": "asked for a person"}})
    assert hw.escalated({"escalation": True})
    for no in ({}, {"escalated": False}, {"escalated": "false"}, {"escalation": {}}, {"escalation": None}, {"escalation": {"transferred": False}}, {"escalation": "false"}):
        assert not hw.escalated(no), no


def test_field_extraction_is_tolerant_but_never_infers_a_qualification() -> None:
    assert hw.qualification({"qualification": "Needs-Human-Review"}) == "needs_human_review"
    assert hw.qualification({"lead": {"qualification_status": "qualified"}}) == "qualified"
    assert hw.qualification({"qualification": "maybe"}) is None and hw.qualification({}) is None
    assert hw.klaros_lead_id({"klarosLeadId": "a"}) == "a" and hw.klaros_lead_id({"lead": {"klaros_lead_id": "b"}}) == "b"
    assert hw.call_id({"callSid": "CA1"}) == "CA1"
    assert hw.klaros_lead_id({"klarosLeadId": "k", "leadId": "h"}) == "k" and hw.halla_lead_id({"leadId": "h"}) == "h"  # camelCase payloads
    assert hw.qualification({"qualificationStatus": "needs_human_review"}) == "needs_human_review"
    f = hw.appointment_facts({"appointment": {"id": "ap1", "scheduledTime": "2026-12-01T10:00:00Z", "service": "Consult"}})
    assert f and f.external_id == "ap1" and f.start_time.year == 2026 and hw.appointment_facts({"x": 1}) is None


# ------------------------------------------------- connection, credentials, tenancy (MOCKED HTTP)


async def test_connected_only_after_a_real_health_request_and_credentials_never_leak(client, halla) -> None:
    token = await _register(client, "Conn Co", "connco@example.com")
    r = await _connect(client, token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "CONNECTED" and body["mode"] == "live"
    assert len(halla.sent("GET", "/integrations/klaros/health")) == 1  # a real request decided it
    assert halla.sent("GET", "/integrations/klaros/health")[0].headers["X-Test-Key"] == API_KEY  # tenant credential, server-side
    # nothing secret anywhere in any response
    setup = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
    dump = json.dumps([body, setup])
    assert API_KEY not in dump and SECRET not in dump
    assert setup["halla"] == {**setup["halla"], "halla_tenant_id": HALLA_TENANT, "has_credential": True, "has_signing_secret": True}
    tid = await _tenant_id(client, token)
    assert setup["halla"]["webhook_url"] == f"https://klaros.test/api/v1/webhooks/halla/{tid}"
    # encrypted at rest, with the existing credential store
    from app.db.session import async_session_maker
    from app.models.integration import IntegrationConnection
    from sqlalchemy import select

    async with async_session_maker() as s:
        row = (await s.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tid, IntegrationConnection.provider == "halla"))).scalar_one()
    assert API_KEY not in row.encrypted_credential and SECRET not in row.encrypted_credential
    assert decrypt_credential(row.encrypted_credential) == {"api_key": API_KEY, "signing_secret": SECRET, "halla_tenant_id": HALLA_TENANT}
    assert row.external_account_id == HALLA_TENANT


async def test_a_failing_health_request_is_never_reported_as_connected(client, halla) -> None:
    token = await _register(client, "Bad Key", "badkey@example.com")
    halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.Response(401)
    r = await _connect(client, token)
    assert r.status_code == 200 and r.json()["status"] == "ERROR" and "rejected the credential" in r.json()["message"]
    halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.Response(200, json={"tenant_id": "someone-else"})
    r = await _connect(client, token)
    assert r.json()["status"] == "NEEDS_ATTENTION" and "different tenant" in r.json()["message"]
    halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.ConnectTimeout("t")
    assert (await client.post(f"{BASE}/workforce/halla/health", headers=_h(token))).json()["status"] == "ERROR"
    del halla.overrides[("GET", "/api/v1/integrations/klaros/health")]
    assert (await client.post(f"{BASE}/workforce/halla/health", headers=_h(token))).json()["status"] == "CONNECTED"  # recovers only when a real request succeeds
    halla.tenant_in_health = None  # a Halla that reports no tenant cannot contradict the mapping
    assert (await client.post(f"{BASE}/workforce/halla/health", headers=_h(token))).json()["status"] == "CONNECTED"


async def test_a_connection_without_a_credential_or_before_connecting_is_not_configured(client, halla) -> None:
    token = await _register(client, "Fresh Co", "freshco@example.com")
    r = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
    assert r["status"]["status"] == "NOT_CONNECTED" and r["status"]["adapter_implemented"] is True and r["status"]["mode"] == "live"
    assert halla.requests == []  # asking for status with no connection makes no Halla call
    assert (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))).status_code == 409
    await _connect(client, token)
    assert (await client.delete(f"{BASE}/workforce/halla/connection", headers=_h(token))).json()["status"] == "NOT_CONNECTED"
    assert (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))).status_code == 409  # credential gone


async def test_a_stale_connected_status_is_reproven_with_a_live_request(client, halla) -> None:
    from app.core.config import get_settings

    token = await _register(client, "Ttl Co", "ttlco@example.com")
    await _connect(client, token)
    n = len(halla.requests)
    await client.get(f"{BASE}/workforce/setup", headers=_h(token))
    assert len(halla.requests) == n  # fresh: no extra request
    get_settings().HALLA_STATUS_TTL_SECONDS = 0
    try:
        halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.Response(503)
        st = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()["status"]
        assert st["status"] == "ERROR"  # the stored CONNECTED was not trusted
    finally:
        get_settings().HALLA_STATUS_TTL_SECONDS = 300


async def test_connect_validates_input_and_needs_the_manage_permission(client, halla) -> None:
    owner = await _register(client, "Rbac Halla", "rbachalla@example.com")
    for bad in ({"halla_tenant_id": "bad id!", "api_key": API_KEY, "signing_secret": SECRET}, {"halla_tenant_id": "t", "api_key": "short", "signing_secret": SECRET}, {"halla_tenant_id": "t", "api_key": API_KEY, "signing_secret": "sh ort"}):
        assert (await client.put(f"{BASE}/workforce/halla/connection", json=bad, headers=_h(owner))).status_code == 422
    tech = await _tech_token(client, owner, "techhalla@example.com")
    assert (await _connect(client, tech)).status_code == 403
    assert (await client.put(f"{BASE}/workforce/halla/connection", json={})).status_code in (401, 403)
    assert halla.requests == []


async def test_tenants_have_separate_connections_credentials_and_secrets(client, halla) -> None:
    a = await _register(client, "Halla A", "halla-a@example.com")
    b = await _register(client, "Halla B", "halla-b@example.com")
    await _connect(client, a)
    st_b = (await client.get(f"{BASE}/workforce/setup", headers=_h(b))).json()
    assert st_b["status"]["status"] == "NOT_CONNECTED" and st_b["halla"]["has_credential"] is False and st_b["halla"]["halla_tenant_id"] is None
    assert (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(b))).status_code == 409  # B cannot ride on A's credential
    lead_a = await _lead(client, a)
    assert (await client.post(f"{BASE}/leads/{lead_a}/halla/sync", headers=_h(b))).status_code in (404, 409)
    await _connect(client, b, halla_tenant="halla-tenant-bbbb", api_key="b-api-key-0123456789", secret="b-secret-0123456789")
    sent_keys = {r.headers["X-Test-Key"] for r in halla.requests}
    assert sent_keys == {API_KEY, "b-api-key-0123456789"}  # each request used its own tenant's key
    ta, tb = await _tenant_id(client, a), await _tenant_id(client, b)
    raw = _body("lead.qualified", tenant="halla-tenant-bbbb", data={"klaros_lead_id": lead_a, "qualification": "qualified"})
    # B's Halla tenant, B's secret — delivered to B's endpoint — naming A's lead: nothing of A's changes.
    r = await _deliver(client, tb, raw, secret="b-secret-0123456789")
    assert r.status_code == 200
    after = (await client.get(f"/api/v1/leads/{lead_a}", headers=_h(a))).json()
    assert (after.get("lead") or after)["status"] == "NEW"
    # B's tenant id on A's URL → mismatch; A's secret on B's URL → bad signature.
    assert (await _deliver(client, ta, raw, secret=SECRET)).status_code == 403
    assert (await _deliver(client, tb, _body("call.started"), secret=SECRET)).status_code == 401


# --------------------------------------------------- workforce, agents, leads, calls (MOCKED HTTP)


async def test_workforce_get_put_and_agents(client, halla) -> None:
    await _seed_registry()
    token = await _register(client, "Wf Halla", "wfhalla@example.com")
    await _connect(client, token)
    await client.post(f"{BASE}/modules/medical_tourism/enable", headers=_h(token))
    for path, body in (("/procedures", {"name": "Rhinoplasty", "category": "Aesthetic"}),):
        assert (await client.post(f"/api/v1/medical-tourism{path}", json=body, headers=_h(token))).status_code == 200
    r = await client.post(f"{BASE}/workforce/halla/configure", headers=_h(token))
    assert r.status_code == 200, r.text
    put = halla.sent("PUT", "/integrations/klaros/workforce")[0]
    sent = json.loads(put.content)
    assert sent["source"] == "klaros" and "Rhinoplasty" in sent["servicesOffered"]
    assert "Complex medical questions" in sent["escalationTriggers"] and sent["autoTransferEnabled"] is True
    assert any(q.startswith("Ask for:") for q in sent["qualificationQuestions"]) and sent["bookingRules"]
    assert r.json()["sent"]["servicesOffered"] == 1  # a count, not the content
    agents = (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))).json()["agents"]
    assert [(a["id"], a["name"], a["available"]) for a in agents] == [("ag1", "Receptionist", True), ("ag2", "Follow-up", None)]  # the junk row is dropped
    assert json.dumps(agents).count("systemPrompt") == 0
    halla.overrides[("GET", "/api/v1/integrations/klaros/agents")] = httpx.Response(500)
    assert (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))).status_code == 502
    halla.overrides[("PUT", "/api/v1/integrations/klaros/workforce")] = httpx.Response(403)
    assert (await client.post(f"{BASE}/workforce/halla/configure", headers=_h(token))).status_code == 502


async def test_lead_sync_carries_klaros_lead_id_and_stores_the_halla_id_without_replacing_ours(client, halla) -> None:
    token = await _register(client, "Sync Co", "syncco@example.com")
    await _connect(client, token)
    lead_id = await _lead(client, token, name="Omar Farsi", phone="+971500000001")
    r = await client.post(f"{BASE}/leads/{lead_id}/halla/sync", headers=_h(token))
    assert r.status_code == 200 and r.json() == {"synced": True, "created": True}
    post = halla.sent("POST", "/api/v1/leads")[0]
    sent = json.loads(post.content)
    assert sent["klarosLeadId"] == lead_id and sent["phoneNumber"] == "+971500000001" and sent["name"] == "Omar Farsi"
    assert sent["metadata"] == {"klaros_lead_id": lead_id}
    assert "id" not in sent and "tenant_id" not in json.dumps(sent)  # no internal ids, no tenant
    lead = (await client.get(f"/api/v1/leads/{lead_id}", headers=_h(token))).json()
    assert (lead.get("lead") or lead)["id"] == lead_id  # Klaros' id is still the identity
    from app.db.session import async_session_maker
    from app.models.crm import Lead

    async with async_session_maker() as s:
        row = await s.get(Lead, uuid.UUID(lead_id))
    assert (row.external_provider, row.external_id) == ("halla", "halla-lead-1")
    # second sync updates the Halla lead (PUT) and still carries klarosLeadId
    r2 = await client.post(f"{BASE}/leads/{lead_id}/halla/sync", headers=_h(token))
    assert r2.json() == {"synced": True, "created": False}
    put = halla.sent("PUT", "/api/v1/leads/halla-lead-1")[0]
    assert json.loads(put.content)["klarosLeadId"] == lead_id
    assert len(halla.sent("POST", "/api/v1/leads")) == 1
    # failures are visible and change nothing
    halla.overrides[("POST", "/api/v1/leads")] = httpx.Response(503)
    other = await _lead(client, token, name="No Sync")
    assert (await client.post(f"{BASE}/leads/{other}/halla/sync", headers=_h(token))).status_code == 502
    async with async_session_maker() as s:
        assert (await s.get(Lead, uuid.UUID(other))).external_id is None
    assert (await client.post(f"{BASE}/leads/{uuid.uuid4()}/halla/sync", headers=_h(token))).status_code == 404


async def test_outbound_call_carries_klaros_lead_id_and_needs_a_phone(client, halla) -> None:
    token = await _register(client, "Call Co", "callco@example.com")
    await _connect(client, token)
    lead_id = await _lead(client, token, phone="+971500000002")
    r = await client.post(f"{BASE}/leads/{lead_id}/halla/call", json={"reason": "qualification", "opening_context": "Following up on your treatment enquiry."}, headers=_h(token))
    assert r.status_code == 200 and r.json() == {"requested": True, "call_id": "CA123"}
    sent = json.loads(halla.sent("POST", "/calls/outbound")[0].content)
    assert sent == {"toNumber": "+971500000002", "reason": "qualification", "klarosLeadId": lead_id, "openingContext": "Following up on your treatment enquiry."}
    nophone = await _lead(client, token, name="No Phone", phone=None)
    assert (await client.post(f"{BASE}/leads/{nophone}/halla/call", headers=_h(token))).status_code == 422
    assert len(halla.sent("POST", "/calls/outbound")) == 1
    assert (await client.post(f"{BASE}/leads/{uuid.uuid4()}/halla/call", headers=_h(token))).status_code == 404


async def test_halla_routes_do_not_exist_unless_the_halla_adapter_is_enabled(client) -> None:
    token = await _register(client, "Off Co", "offco@example.com")
    assert (await _connect(client, token)).status_code == 404
    assert (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))).status_code == 404
    assert (await client.post(f"{BASE}/workforce/halla/health", headers=_h(token))).status_code == 404
    assert "halla" not in (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()


async def test_misconfigured_deployment_is_reported_not_guessed(client, halla) -> None:
    from app.core.config import get_settings

    token = await _register(client, "Cfg Co", "cfgco@example.com")
    get_settings().HALLA_API_KEY_HEADER = None  # the credential header comes from Halla's contract; no default
    st = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()["status"]
    assert st["status"] == "CONFIGURATION_REQUIRED" and "operator" in st["message"]
    r = await _connect(client, token)
    assert r.json()["status"] == "CONFIGURATION_REQUIRED"
    assert halla.requests == []  # nothing was sent anywhere


# -------------------------------------------- the webhook receiver (real Klaros code, test-signed)


async def _connected(client, name, email):
    token = await _register(client, name, email)
    assert (await _connect(client, token)).json()["status"] == "CONNECTED"
    return token, await _tenant_id(client, token)


async def _lead_state(client, token, lead_id):
    j = (await client.get(f"/api/v1/leads/{lead_id}", headers=_h(token))).json()
    j = j.get("lead") or j
    return j["status"], j["qualification_status"]


async def test_a_valid_event_is_applied_and_every_forgery_is_rejected(client, halla) -> None:
    token, tid = await _connected(client, "Hook Co", "hookco@example.com")
    lead = await _lead(client, token)
    raw = _body("lead.qualified", data={"klaros_lead_id": lead, "qualification": "qualified", "summary": "Wants rhinoplasty next month."})
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}
    assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")
    # modified body, wrong secret, missing / stale / malformed timestamp: all rejected, nothing applied
    other = await _lead(client, token, name="Other")
    forged = _body("lead.qualified", data={"klaros_lead_id": other, "qualification": "qualified"})
    ts = str(int(time.time()))
    good_sig = hw.sign(SECRET, ts, forged)
    assert (await _deliver(client, tid, forged + b" ", ts=ts, sig=good_sig)).status_code == 401
    assert (await _deliver(client, tid, forged, secret="wrong-secret-0123456789")).status_code == 401
    assert (await _deliver(client, tid, forged, sig="")).status_code == 401
    assert (await _deliver(client, tid, forged, ts="garbage")).status_code == 401
    old = str(int(time.time()) - 7200)
    assert (await _deliver(client, tid, forged, ts=old, sig=hw.sign(SECRET, old, forged))).status_code == 401
    assert await _lead_state(client, token, other) == ("NEW", "PENDING")


async def test_unknown_unsupported_and_malformed_events_and_unknown_tenants(client, halla) -> None:
    token, tid = await _connected(client, "Odd Co", "oddco@example.com")
    assert (await _deliver(client, tid, _body("appointment.requested"))).status_code == 400  # Halla never emits it; Klaros does not assume it
    assert (await _deliver(client, tid, _body("billing.invoice.paid"))).status_code == 400
    assert (await _deliver(client, tid, b"{not json")).status_code == 400
    assert (await _deliver(client, tid, json.dumps({"type": "call.started"}).encode())).status_code == 400
    assert (await _deliver(client, uuid.uuid4(), _body("call.started"))).status_code == 404  # no such connection
    await client.delete(f"{BASE}/workforce/halla/connection", headers=_h(token))
    assert (await _deliver(client, tid, _body("call.started"))).status_code == 404  # disconnected: secret is gone


async def test_event_tenant_must_match_the_connected_halla_tenant(client, halla) -> None:
    token, tid = await _connected(client, "Mismatch Co", "mismatchco@example.com")
    lead = await _lead(client, token)
    raw = _body("lead.qualified", tenant="some-other-halla-tenant", data={"klaros_lead_id": lead, "qualification": "qualified"})
    assert (await _deliver(client, tid, raw)).status_code == 403  # authentic signature, wrong tenant
    assert await _lead_state(client, token, lead) == ("NEW", "PENDING")


async def test_duplicate_events_change_and_trigger_nothing_twice(client, halla, event_bus) -> None:
    token, tid = await _connected(client, "Dup Co", "dupco@example.com")
    assert (await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))).json()["created"] is True
    lead = await _lead(client, token)
    raw = _body("lead.escalated", eid="evt-dup-1", data={"klaros_lead_id": lead, "summary": "Asked for a person."})
    first = await _deliver(client, tid, raw)
    second = await _deliver(client, tid, raw)
    assert first.json() == {"status": "ok"} and second.json() == {"status": "duplicate_ignored"}
    await event_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert [e["type"] for e in inter["events"]] == ["halla.lead.escalated"]
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    wf = next(w for w in ops["workflows"] if w["name"] == "Escalated lead alert")
    assert wf["runs"] == 1  # one delivery, one workflow run


async def test_escalation_enters_the_existing_workflow_engine_and_the_operating_layer(client, halla, event_bus) -> None:
    token, tid = await _connected(client, "Esc Hook", "eschook@example.com")
    await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))
    lead = await _lead(client, token)
    assert (await _deliver(client, tid, _body("lead.escalated", data={"klaros_lead_id": lead, "summary": "Complex question about medication."}))).status_code == 200
    await event_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    assert await _lead_state(client, token, lead) == ("NEW", "REQUIRES_HUMAN")
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    assert ops["ai"]["escalated"] == 1 and ops["ai"]["needs_person"] == 1
    wf = next(w for w in ops["workflows"] if w["name"] == "Escalated lead alert")
    assert wf["trigger_event"] == "halla.lead.escalated" and wf["runs"] == 1 and wf["last_run"]["status"] == "COMPLETED"
    notes = (await client.get("/api/v1/notifications", headers=_h(token))).json()["notifications"]
    assert any(n["title"] == "A lead needs a person" for n in notes)
    board = (await client.get(f"{BASE}/leads", headers=_h(token))).json()["leads"][0]
    assert board["ai_state"] == "ESCALATED" and board["next_action"]["text"] == "A person needs to contact this customer"
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert inter["events"][0]["simulated"] is False  # real events are never labelled simulated
    # the escalation starter is idempotent
    assert (await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))).json()["created"] is False
    assert (await client.post(f"{BASE}/workflows/starter?kind=bogus", headers=_h(token))).status_code == 422


@pytest.mark.parametrize(
    "qualification,expect",
    [("qualified", ("QUALIFIED", "QUALIFIED")), ("not_qualified", ("UNQUALIFIED", "UNQUALIFIED")), ("needs_human_review", ("NEW", "REQUIRES_HUMAN")), ("unknown", ("NEW", "PENDING"))],
)
async def test_qualification_maps_halla_states_onto_the_existing_lead_model(client, halla, qualification, expect) -> None:
    token, tid = await _connected(client, f"Q {qualification}", f"q-{qualification}@example.com")
    lead = await _lead(client, token)
    raw = _body("lead.qualified", data={"klaros_lead_id": lead, "qualification": qualification})
    assert (await _deliver(client, tid, raw)).status_code == 200
    assert await _lead_state(client, token, lead) == expect


async def test_a_machine_never_pulls_a_lead_backwards_and_unknown_never_overwrites(client, halla) -> None:
    token, tid = await _connected(client, "Strong Co", "strongco@example.com")
    lead = await _lead(client, token)
    await client.patch(f"/api/v1/leads/{lead}", json={"status": "BOOKED"}, headers=_h(token))
    await _deliver(client, tid, _body("lead.qualified", data={"klaros_lead_id": lead, "qualification": "not_qualified"}))
    assert (await _lead_state(client, token, lead))[0] == "BOOKED"  # a booked lead is not demoted
    q = await _lead(client, token, name="Qualified")
    await _deliver(client, tid, _body("lead.qualified", data={"klaros_lead_id": q, "qualification": "qualified"}))
    await _deliver(client, tid, _body("call.completed", data={"klaros_lead_id": q, "call_id": "c1", "qualification": "unknown"}))
    await _deliver(client, tid, _body("lead.qualified", data={"klaros_lead_id": q, "qualification": "not_qualified"}))
    assert await _lead_state(client, token, q) == ("QUALIFIED", "QUALIFIED")


async def test_call_events_update_state_without_copying_transcripts(client, halla) -> None:
    token, tid = await _connected(client, "Call Hook", "callhook@example.com")
    lead = await _lead(client, token)
    await _deliver(client, tid, _body("call.started", data={"klaros_lead_id": lead, "call_id": "CA9"}))
    assert (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()["state"] == "IN_PROGRESS"
    done = _body("call.completed", data={"klaros_lead_id": lead, "call_id": "CA9", "qualification": "qualified", "summary": "Hair transplant in Turkey, 2500 USD.", "outcome": "completed", "transcript": "SECRET FULL TRANSCRIPT TEXT"})
    await _deliver(client, tid, done)
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert inter["state"] == "QUALIFICATION_PENDING" and inter["summary"] == "Hair transplant in Turkey, 2500 USD."
    assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")  # qualification Halla supplied, applied
    assert "SECRET FULL TRANSCRIPT" not in json.dumps(inter)
    from app.db.session import async_session_maker
    from app.models.event import Event
    from app.models.integration import WebhookEvent
    from sqlalchemy import select

    async with async_session_maker() as s:
        stored = json.dumps([e.payload for e in (await s.execute(select(Event))).scalars()] + [w.raw_payload for w in (await s.execute(select(WebhookEvent))).scalars()])
    assert "SECRET FULL TRANSCRIPT" not in stored  # the transcript is not stored anywhere in Klaros
    # an escalated call.completed is an escalation
    esc = _body("call.completed", data={"klaros_lead_id": lead, "call_id": "CA10", "escalated": True})
    await _deliver(client, tid, esc)
    assert (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()["state"] == "ESCALATED"


async def test_appointment_events_mirror_halla_bookings_once(client, halla) -> None:
    token, tid = await _connected(client, "Appt Co", "apptco@example.com")
    lead = await _lead(client, token)
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    data = {"klaros_lead_id": lead, "appointment": {"id": "ap-1", "scheduled_time": start.isoformat(), "service": "Consultation"}}
    confirmed = _body("appointment.confirmed", data=data)
    assert (await _deliver(client, tid, confirmed)).json() == {"status": "ok"}
    assert (await _deliver(client, tid, confirmed)).json() == {"status": "duplicate_ignored"}
    from app.db.session import async_session_maker
    from app.models.crm import Appointment
    from sqlalchemy import select

    async def appts():
        async with async_session_maker() as s:
            return list((await s.execute(select(Appointment).where(Appointment.tenant_id == tid))).scalars())

    rows = await appts()
    assert len(rows) == 1 and (rows[0].external_provider, rows[0].external_id, rows[0].status) == ("halla", "ap-1", "CONFIRMED")
    assert str(rows[0].lead_id) == lead and rows[0].title.startswith("Consultation") and rows[0].start_time.replace(tzinfo=timezone.utc) == start
    assert (await _lead_state(client, token, lead))[0] == "BOOKED"
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert inter["state"] == "APPOINTMENT_CONFIRMED"
    # a different delivery of the same booking (new event id) must not duplicate the appointment
    await _deliver(client, tid, _body("appointment.confirmed", data=data))
    assert len(await appts()) == 1
    # rescheduled → same row, new time; cancelled → CANCELLED
    later = start + timedelta(days=1)
    await _deliver(client, tid, _body("appointment.rescheduled", data={**data, "appointment": {**data["appointment"], "scheduled_time": later.isoformat()}}))
    rows = await appts()
    assert len(rows) == 1 and rows[0].start_time.replace(tzinfo=timezone.utc) == later and rows[0].status == "CONFIRMED"
    await _deliver(client, tid, _body("appointment.cancelled", data=data))
    rows = await appts()
    assert rows[0].status == "CANCELLED"
    assert (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()["state"] == "APPOINTMENT_CANCELLED"
    # a cancellation for a booking Klaros never saw is a harmless no-op; a booking with no time is not invented
    assert (await _deliver(client, tid, _body("appointment.cancelled", data={"klaros_lead_id": lead, "appointment": {"id": "never-seen"}}))).status_code == 200
    assert (await _deliver(client, tid, _body("appointment.confirmed", data={"klaros_lead_id": lead, "appointment": {"id": "no-time"}}))).status_code == 200
    assert len(await appts()) == 1


async def test_lead_created_links_by_klaros_id_and_follows_klaros_intake_policy_otherwise(client, halla) -> None:
    token, tid = await _connected(client, "Link Co", "linkco@example.com")
    lead = await _lead(client, token, phone="+971500000009")
    await _deliver(client, tid, _body("lead.created", data={"klaros_lead_id": lead, "lead_id": "h-77"}))
    from app.db.session import async_session_maker
    from app.models.crm import Lead
    from sqlalchemy import func, select

    async with async_session_maker() as s:
        row = await s.get(Lead, uuid.UUID(lead))
        assert (row.external_provider, row.external_id) == ("halla", "h-77")
        total = (await s.execute(select(func.count()).select_from(Lead).where(Lead.tenant_id == tid))).scalar_one()
    assert total == 1  # linked, not duplicated — even though the phone matches
    # no Klaros id: created through the normal intake path, once, keyed on Halla's lead id
    phone_only = {"lead_id": "h-88", "lead": {"id": "h-88", "name": "Walk-in Caller", "phone": "+971500000009"}}
    await _deliver(client, tid, _body("lead.created", data=phone_only))
    await _deliver(client, tid, _body("lead.created", data=phone_only))  # a second delivery with a new event id
    async with s_ctx() as s:
        rows = (await s.execute(select(Lead).where(Lead.tenant_id == tid))).scalars().all()
    created = [r for r in rows if r.external_id == "h-88"]
    assert len(created) == 1 and created[0].source == "VOICE" and created[0].name == "Walk-in Caller"
    # not enough to create a lead from (no name / no contact): nothing is invented
    assert (await _deliver(client, tid, _body("lead.created", data={"lead_id": "h-99"}))).status_code == 200
    async with s_ctx() as s:
        assert not [r for r in (await s.execute(select(Lead).where(Lead.tenant_id == tid))).scalars() if r.external_id == "h-99"]


def s_ctx():
    from app.db.session import async_session_maker

    return async_session_maker()


async def test_events_for_leads_klaros_cannot_resolve_are_acknowledged_without_creating_anything(client, halla) -> None:
    token, tid = await _connected(client, "Orphan Co", "orphanco@example.com")
    for etype in ("call.started", "call.completed", "lead.qualified", "lead.escalated", "lead.updated"):
        r = await _deliver(client, tid, _body(etype, data={"lead_id": "unknown-h", "call_id": "x", "qualification": "qualified"}))
        assert r.status_code == 200
    assert (await client.get(f"{BASE}/leads", headers=_h(token))).json()["total"] == 0


async def test_provider_matching_and_workflows_stay_the_existing_implementation(client, halla) -> None:
    """A qualified Halla event on a patient lead feeds the existing provider matching — nothing new is involved."""
    await _seed_registry()
    token, tid = await _connected(client, "Mt Hook", "mthook@example.com")
    await client.post(f"{BASE}/modules/medical_tourism/enable", headers=_h(token))
    proc = (await client.post("/api/v1/medical-tourism/procedures", json={"name": "Rhinoplasty", "category": "Aesthetic", "idempotency_key": "p1"}, headers=_h(token))).json()["procedure"]
    prov = (await client.post("/api/v1/medical-tourism/providers", json={"name": "Apex Hospital", "country": "IN", "city": "Delhi", "idempotency_key": "v1"}, headers=_h(token))).json()["provider"]
    await client.post("/api/v1/medical-tourism/offerings", json={"provider_id": prov["id"], "procedure_id": proc["id"], "estimated_price": "3200", "currency": "USD"}, headers=_h(token))
    lead = await _lead(client, token)
    from app.services.medical_tourism_service import MedicalTourismService

    await MedicalTourismService(s_ctx().__class__ and __import__("app.db.session", fromlist=["x"]).async_session_maker).create_patient_lead(tid, uuid.UUID(lead), procedure_id=uuid.UUID(proc["id"]), preferred_destination_country="IN")
    await _deliver(client, tid, _body("lead.qualified", data={"klaros_lead_id": lead, "qualification": "qualified"}))
    ops = (await client.get(f"/api/v1/medical-tourism/leads/{lead}/operations", headers=_h(token))).json()
    assert ops["matching"]["matches"][0]["name"] == "Apex Hospital" and ops["matching"]["matches"][0]["fit"] == "STRONG"
    board = (await client.get(f"{BASE}/leads", headers=_h(token))).json()["leads"][0]
    assert board["ai_state"] == "QUALIFIED" and board["status"] == "QUALIFIED" and board["country"] == "IN"


async def test_an_already_linked_halla_id_is_never_stolen_and_never_causes_a_failing_redelivery(client, halla) -> None:
    token, tid = await _connected(client, "Steal Co", "stealco@example.com")
    first, second = await _lead(client, token, name="First"), await _lead(client, token, name="Second")
    assert (await _deliver(client, tid, _body("lead.updated", data={"klaros_lead_id": first, "lead_id": "h-same"}))).status_code == 200
    assert (await _deliver(client, tid, _body("lead.updated", data={"klaros_lead_id": second, "lead_id": "h-same"}))).status_code == 200  # not a 500
    from app.db.session import async_session_maker
    from app.models.crm import Lead

    async with async_session_maker() as s:
        assert (await s.get(Lead, uuid.UUID(first))).external_id == "h-same"
        assert (await s.get(Lead, uuid.UUID(second))).external_id is None


async def test_an_oversized_webhook_is_refused_before_it_is_read(client, halla) -> None:
    token, tid = await _connected(client, "Big Co", "bigco@example.com")
    big = b"x" * (hw.MAX_BODY_BYTES + 1)
    r = await _deliver(client, tid, big)
    assert r.status_code == 413


async def test_one_escalation_is_one_workflow_run_whichever_halla_event_arrives_first(client, halla, event_bus) -> None:
    """Halla reports an escalation inside call.completed AND as a separate lead.escalated (either may arrive first).
    They describe the same call, so Klaros must run the escalation workflow once."""
    token, tid = await _connected(client, "Esc Order", "escorder@example.com")
    await client.post(f"{BASE}/workflows/starter?kind=escalation", headers=_h(token))
    for order, name in ((("call", "esc"), "A"), (("esc", "call"), "B")):
        lead = await _lead(client, token, name=f"Lead {name}")
        cid = f"CA-{name}"
        for which in order:
            raw = (
                _body("call.completed", data={"klarosLeadId": lead, "callId": cid, "qualificationStatus": "needs_human_review", "escalation": {"transferred": True, "target": "sales"}})
                if which == "call"
                else _body("lead.escalated", data={"klarosLeadId": lead, "callId": cid})
            )
            assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}
        inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
        assert [e["type"] for e in inter["events"]].count("halla.lead.escalated") == 1  # one escalation event
        assert inter["state"] == "ESCALATED"
    await event_bus.process_pending(EventType.HALLA_LEAD_ESCALATED)
    ops = (await client.get(f"{BASE}/operations", headers=_h(token))).json()
    wf = next(w for w in ops["workflows"] if w["name"] == "Escalated lead alert")
    assert wf["runs"] == 2  # two leads, one run each — never two for the same call


async def test_agents_listing_passes_nothing_sensitive_through_even_if_halla_sends_it(client, halla) -> None:
    token, _ = await _connected(client, "Agents Safe", "agentssafe@example.com")
    halla.overrides[("GET", "/api/v1/integrations/klaros/agents")] = httpx.Response(
        200, json={"agents": [{"id": "ag1", "name": "Receptionist", "systemPrompt": "TOP SECRET PROMPT", "transferNumber": "+15550009999", "apiKey": "leak"}]}
    )
    r = await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))
    assert r.json()["agents"] == [{"id": "ag1", "name": "Receptionist", "role": None, "status": None, "available": None}]
    assert "TOP SECRET" not in r.text and "5550009999" not in r.text and "leak" not in r.text
    # and the sanitised shape (no such fields at all) works
    halla.overrides[("GET", "/api/v1/integrations/klaros/agents")] = httpx.Response(200, json={"agents": [{"id": "ag2", "name": "Follow-up"}]})
    assert (await client.get(f"{BASE}/workforce/halla/agents", headers=_h(token))).json()["agents"][0]["name"] == "Follow-up"


async def test_a_halla_booking_never_emits_the_core_appointment_created_event_or_goes_back_to_halla(client, halla) -> None:
    token, tid = await _connected(client, "No Echo", "noecho@example.com")
    lead = await _lead(client, token)
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
    before = len(halla.requests)
    await _deliver(client, tid, _body("appointment.confirmed", data={"klarosLeadId": lead, "appointment": {"id": "ap-echo", "scheduledTime": start}}))
    from app.db.session import async_session_maker
    from app.models.event import Event
    from sqlalchemy import select

    async with async_session_maker() as s:
        types = [e.event_type for e in (await s.execute(select(Event).where(Event.tenant_id == tid))).scalars()]
    assert "appointment.created" not in types and "halla.appointment.confirmed" in types
    assert len(halla.requests) == before  # nothing was sent back to Halla


async def test_qualification_and_call_completed_in_halla_order_end_in_the_final_outcome(client, halla) -> None:
    """The correct Halla sequence: qualification first, then call.completed carrying the same outcome."""
    token, tid = await _connected(client, "Order OK", "orderok@example.com")
    lead = await _lead(client, token)
    t1 = datetime.now(timezone.utc) - timedelta(seconds=30)
    await _deliver(client, tid, _body("lead.qualified", at=t1, data={"klarosLeadId": lead, "callId": "C1", "qualificationStatus": "qualified"}))
    await _deliver(client, tid, _body("call.completed", at=t1 + timedelta(seconds=5), data={"klarosLeadId": lead, "callId": "C1", "qualificationStatus": "qualified", "summary": "done"}))
    assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert [e["type"] for e in inter["events"]] == ["halla.lead.qualified", "halla.interaction.completed"]


async def test_a_late_stale_lead_qualified_never_overrides_the_newer_outcome_from_call_completed(client, halla) -> None:
    """The defect Halla fixed on its side could still surface through a retry: lead.qualified (older) delivered AFTER
    call.completed (newer). Klaros orders by Halla's own timestamps and ignores the stale one."""
    token, tid = await _connected(client, "Order Late", "orderlate@example.com")
    lead = await _lead(client, token)
    t1 = datetime.now(timezone.utc) - timedelta(seconds=60)
    done = _body("call.completed", at=t1 + timedelta(seconds=10), data={"klarosLeadId": lead, "callId": "C2", "qualificationStatus": "needs_human_review"})
    late = _body("lead.qualified", at=t1, data={"klarosLeadId": lead, "callId": "C2", "qualificationStatus": "qualified"})
    assert (await _deliver(client, tid, done)).status_code == 200
    assert (await _deliver(client, tid, late)).json() == {"status": "ok"}  # acknowledged, so Halla stops retrying it
    assert await _lead_state(client, token, lead) == ("NEW", "REQUIRES_HUMAN")  # the newer outcome stands
    inter = (await client.get(f"{BASE}/leads/{lead}/interaction", headers=_h(token))).json()
    assert "halla.lead.qualified" not in [e["type"] for e in inter["events"]]  # and the stale event is not recorded as a qualification
    # a stale call.completed qualification likewise does not win; the call itself is still recorded
    lead2 = await _lead(client, token, name="Second")
    await _deliver(client, tid, _body("lead.qualified", at=t1 + timedelta(seconds=30), data={"klarosLeadId": lead2, "qualificationStatus": "qualified"}))
    await _deliver(client, tid, _body("call.completed", at=t1, data={"klarosLeadId": lead2, "callId": "C3", "qualificationStatus": "not_qualified"}))
    assert await _lead_state(client, token, lead2) == ("QUALIFIED", "QUALIFIED")


async def test_qualification_failure_unknown_and_retry_never_demote_or_duplicate(client, halla) -> None:
    token, tid = await _connected(client, "Order Unknown", "orderunknown@example.com")
    lead = await _lead(client, token)
    t = datetime.now(timezone.utc) - timedelta(seconds=20)
    await _deliver(client, tid, _body("lead.qualified", at=t, data={"klarosLeadId": lead, "qualificationStatus": "qualified"}, eid="q1"))
    # qualification service failed on Halla's side: call.completed arrives with `unknown` (or nothing)
    await _deliver(client, tid, _body("call.completed", at=t + timedelta(seconds=3), data={"klarosLeadId": lead, "callId": "C4", "qualificationStatus": "unknown"}))
    await _deliver(client, tid, _body("call.completed", at=t + timedelta(seconds=4), data={"klarosLeadId": lead, "callId": "C5"}))
    assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")
    # a redelivery of the original (same event id) is a duplicate
    assert (await _deliver(client, tid, _body("lead.qualified", at=t, data={"klarosLeadId": lead, "qualificationStatus": "qualified"}, eid="q1"))).json() == {"status": "duplicate_ignored"}


async def test_klaros_automatic_scoring_never_overwrites_a_decision_the_workforce_already_made(client, halla, event_bus) -> None:
    """Found on live PostgreSQL: the automatic qualification a new lead triggers runs asynchronously and used to land AFTER
    Halla's outcome, silently overwriting it. It now records its score but never overrides a decided lead; an explicit
    'qualify this lead' action still always applies."""
    token, tid = await _connected(client, "Race Co", "raceco@example.com")
    lead = await _lead(client, token)  # publishes lead.created, whose automatic qualification has not run yet
    assert (await _deliver(client, tid, _body("lead.qualified", data={"klarosLeadId": lead, "qualificationStatus": "needs_human_review"}))).status_code == 200
    assert await _lead_state(client, token, lead) == ("NEW", "REQUIRES_HUMAN")
    await event_bus.process_pending(EventType.LEAD_CREATED)  # the late automatic qualification
    assert await _lead_state(client, token, lead) == ("NEW", "REQUIRES_HUMAN")  # Halla's decision survives
    j = (await client.get(f"/api/v1/leads/{lead}", headers=_h(token))).json()
    assert (j.get("lead") or j)["lead_score"] is not None  # …but the score was still computed and recorded
    # control: a lead nobody has decided is still auto-qualified exactly as before
    other = await _lead(client, token, name="Undecided")
    await event_bus.process_pending(EventType.LEAD_CREATED)
    assert (await _lead_state(client, token, other))[1] != "PENDING"
    # an explicit qualify action is a decision and always applies
    r = await client.post(f"/api/v1/leads/{lead}/qualify", headers=_h(token))
    assert r.status_code == 200, r.text
    body = r.json()
    outcome = body.get("qualification_status") or (body.get("outcome") or {}).get("qualification_status") or (body.get("result") or {}).get("qualification_status")
    assert outcome is not None and (await _lead_state(client, token, lead))[1] == outcome  # the explicit action decided, and it stuck


async def test_a_provider_can_have_its_own_verification_timeout_and_a_slow_one_is_an_error_not_a_hang(client) -> None:
    import asyncio

    from app.db.session import async_session_maker
    from app.services.integration_connection_service import IntegrationConnectionService

    token = await _register(client, "Timeout Co", "timeoutco@example.com")
    tid = await _tenant_id(client, token)
    calls = []

    async def slow(_cred):
        calls.append(1)
        await asyncio.sleep(0.4)
        return True, "ok"

    svc = IntegrationConnectionService(async_session_maker)
    svc.register_verifier("slowprov", slow, timeout=0.05)
    svc.register_verifier("patientprov", slow, timeout=2.0)
    assert (await svc.connect(tid, "slowprov", {"k": "v"}, created_by=None)).status == "ERROR"  # cut off by its own, shorter limit
    assert (await svc.connect(tid, "patientprov", {"k": "v"}, created_by=None)).status == "CONNECTED"  # allowed its own, longer one
    from app.core.config import get_settings

    assert get_settings().HALLA_HEALTH_TIMEOUT_SECONDS > IntegrationConnectionService.VERIFY_TIMEOUT_SECONDS  # Halla is allowed longer than the default


def test_halla_health_timeout_is_what_the_verifier_uses(monkeypatch) -> None:
    import asyncio

    from app.core.config import get_settings
    from app.integrations.workforce import halla_adapter

    s = get_settings()
    saved = (s.HALLA_API_BASE_URL, s.HALLA_API_KEY_HEADER, s.HALLA_HEALTH_TIMEOUT_SECONDS)
    s.HALLA_API_BASE_URL, s.HALLA_API_KEY_HEADER, s.HALLA_HEALTH_TIMEOUT_SECONDS = "https://halla.test", "X-Test-Key", 17.0
    seen = {}

    class Spy:
        def __init__(self, endpoint, api_key, **kw):
            seen.update(kw)

        async def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(halla_adapter, "HallaClient", Spy)
    try:
        ok, _ = asyncio.run(halla_adapter.halla_verifier({"api_key": API_KEY, "signing_secret": SECRET, "halla_tenant_id": "t"}))
    finally:
        s.HALLA_API_BASE_URL, s.HALLA_API_KEY_HEADER, s.HALLA_HEALTH_TIMEOUT_SECONDS = saved
    assert ok and seen["timeout"] == 17.0 and seen["max_retries"] == 0


@pytest.mark.parametrize(
    "health,expected_status,expected_text",
    [
        ({"authenticated": True, "tenantExists": True, "status": "healthy"}, "CONNECTED", None),
        ({"authenticated": False, "tenantExists": True, "status": "healthy"}, "ERROR", "did not authenticate"),
        ({"authenticated": True, "tenantExists": False, "status": "healthy"}, "ERROR", "does not know that tenant"),
        ({"authenticated": True, "tenantExists": True, "status": "degraded"}, "ERROR", "degraded"),
        ({"authenticated": True, "tenantExists": True, "status": "healthy", "tenantId": "someone-else"}, "NEEDS_ATTENTION", "different tenant"),
    ],
)
async def test_health_must_actually_say_authenticated_existing_and_healthy(client, halla, health, expected_status, expected_text) -> None:
    """Halla's health answers {authenticated, tenantExists, status}: a 200 with `authenticated:false` is not a connection."""
    token = await _register(client, f"Health {expected_status}", f"health{uuid.uuid4().hex[:5]}@example.com")
    halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.Response(200, json=health)
    r = await _connect(client, token)
    assert r.json()["status"] == expected_status
    if expected_text:
        assert expected_text in r.json()["message"]


async def test_bearer_scheme_is_sent_as_authorization_bearer_key(client, halla) -> None:
    from app.core.config import get_settings

    s = get_settings()
    s.HALLA_API_KEY_HEADER, s.HALLA_API_KEY_SCHEME = "Authorization", "Bearer"
    token = await _register(client, "Bearer Co", "bearerco@example.com")
    assert (await _connect(client, token)).json()["status"] == "CONNECTED"
    sent = halla.sent("GET", "/integrations/klaros/health")[0]
    assert sent.headers["Authorization"] == f"Bearer {API_KEY}"


async def test_operator_script_connects_a_tenant_from_environment_secrets_and_never_prints_them(client, halla, capsys) -> None:
    from scripts.connect_halla_tenant import connect

    token = await _register(client, "Script Co", "scriptco@example.com")
    tid = await _tenant_id(client, token)
    result = await connect(tid, HALLA_TENANT, {"HALLA_API_KEY": API_KEY, "HALLA_WEBHOOK_SECRET": SECRET})
    assert result["status"] == "CONNECTED" and result["halla_tenant_id"] == HALLA_TENANT
    assert result["webhook_url"] == f"https://klaros.test/api/v1/webhooks/halla/{tid}"
    assert API_KEY not in repr(result) and SECRET not in repr(result)
    setup = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
    assert setup["halla"]["has_credential"] and setup["status"]["status"] == "CONNECTED"  # same encrypted connection the UI uses
    with pytest.raises(SystemExit):
        await connect(tid, HALLA_TENANT, {})  # missing secrets refuse, nothing is sent
