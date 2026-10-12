"""Automatic AI-receptionist setup (Klaros side): the owner presses one button; Klaros asks Halla with its own platform secret, keeps the credentials Halla
returns (encrypted) and proves them with a real health request.

LABELLING: Halla is NOT contacted. Every request goes to an `httpx.MockTransport` that records it and answers as Halla's provisioning route is specified to
("MOCKED HTTP"). These tests prove what Klaros SENDS, stores and refuses, not that a deployed Halla accepts it. SQLite + in-process HTTP."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.integrations.credential_store import decrypt_credential
from tests.test_halla_integration import API_KEY, HALLA_TENANT, SECRET, FakeHalla, _h, _tenant_id, halla  # noqa: F401
from tests.test_mt_setup import FORM, _reg

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/business-builder"
PLATFORM_SECRET = "platform-secret-" + "x" * 32


class FakeProvisioner(FakeHalla):
    """FakeHalla plus Halla's provisioning route. `reply` is what the route answers; `calls` records every provisioning request."""

    def __init__(self) -> None:
        super().__init__()
        self.reply: httpx.Response | None = None
        self.phone_replies: dict[str, httpx.Response] = {}  # per action; an action with no entry gets the default answer below

    def _phone(self, request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content)
        action = body.get("action")
        if action in self.phone_replies:
            return self.phone_replies[action]
        data: dict = {
            "status": {"numbers": [], "businessNumber": None, "humanHandoff": False, "canBuy": True, "monthlyCostUsd": 1.15,
                       "plan": {"plan": "professional", "status": "trialing", "trialExpiresAt": "2026-10-26T00:00:00Z", "trialMinutesUsed": 0}},
            "search": {"numbers": [{"phoneNumber": "+14155550123", "friendlyName": "(415) 555-0123"}], "monthlyCostUsd": 1.15},
            "buy": {"phoneNumber": body.get("phoneNumber"), "monthlyCostUsd": 1.15, "receptionistPinned": True},
            "set_business_number": {"businessNumber": body.get("businessNumber"), "humanHandoff": bool(body.get("businessNumber"))},
        }[action]
        return httpx.Response(200, json={"success": True, "data": data})

    @property
    def phone_calls(self) -> list[httpx.Request]:
        return self.sent("POST", "/platform/klaros/phone")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/platform/klaros/phone"):
            self.requests.append(request)
            return self._phone(request)
        if request.url.path.endswith("/platform/klaros/provision"):
            self.requests.append(request)
            if self.reply is not None:
                return self.reply
            return httpx.Response(201, json={"success": True, "data": {"status": "created", "tenantId": HALLA_TENANT, "apiKey": API_KEY, "signingSecret": SECRET}})
        return super().__call__(request)

    @property
    def calls(self) -> list[httpx.Request]:
        return self.sent("POST", "/platform/klaros/provision")


@pytest.fixture
def provisioner(monkeypatch, halla):  # noqa: F811
    from app.core.config import get_settings
    from app.integrations.workforce import halla_client

    s = get_settings()
    saved = s.HALLA_PROVISIONING_SECRET
    s.HALLA_PROVISIONING_SECRET = PLATFORM_SECRET
    fake = FakeProvisioner()
    fake.tenant_in_health = HALLA_TENANT
    transport = httpx.MockTransport(fake)
    original = halla_client.HallaClient

    def factory(endpoint, api_key, **kw):
        kw.setdefault("transport", transport)
        return original(endpoint, api_key, **kw)

    monkeypatch.setattr(halla_client, "HallaClient", factory)
    # the adapter's own (tenant) calls must reach the same fake, so health after provisioning is a real request to it
    from app.integrations.workforce import halla_adapter, registry

    adapter_original = halla_adapter.HallaClient

    def adapter_factory(endpoint, api_key, **kw):
        kw.setdefault("transport", transport)
        return adapter_original(endpoint, api_key, **kw)

    monkeypatch.setattr(halla_adapter, "HallaClient", adapter_factory)
    registry._halla_instance = None
    yield fake
    s.HALLA_PROVISIONING_SECRET = saved


async def _ready_mt(client, email="prov@example.com"):
    token = await _reg(client, "Prov Co", email)
    assert (await client.post("/api/v1/business-setup/medical-tourism", json=FORM, headers=_h(token))).status_code == 200
    return token


async def test_one_button_creates_the_receptionist_and_keeps_the_credentials_encrypted(client, provisioner) -> None:
    token = await _ready_mt(client)
    tid = await _tenant_id(client, token)
    r = await client.post(f"{BASE}/workforce/halla/provision", headers=_h(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "CONNECTED" and body["configured"] is True
    # the browser sees no credential at all, neither Halla's nor the platform secret
    dump = json.dumps(body)
    assert API_KEY not in dump and SECRET not in dump and PLATFORM_SECRET not in dump
    assert body["halla"]["halla_tenant_id"] == HALLA_TENANT and body["halla"]["has_credential"] and body["halla"]["has_signing_secret"]

    # what Klaros sent: the platform secret as a Bearer token, this business's own id and webhook address, a vertical, and nothing else
    (call,) = provisioner.calls
    assert call.headers["Authorization"] == f"Bearer {PLATFORM_SECRET}"
    sent = json.loads(call.content)
    assert sent == {"klarosTenantId": str(tid), "businessName": "Prov Co", "vertical": "medical_tourism", "webhookUrl": f"https://klaros.test/api/v1/webhooks/halla/{tid}", "rotate": True}

    # stored encrypted with the existing credential store, and the business context was sent across at once
    from app.db.session import async_session_maker
    from app.models.integration import IntegrationConnection
    from sqlalchemy import select

    async with async_session_maker() as s:
        row = (await s.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tid, IntegrationConnection.provider == "halla"))).scalar_one()
    assert API_KEY not in row.encrypted_credential
    assert decrypt_credential(row.encrypted_credential) == {"api_key": API_KEY, "signing_secret": SECRET, "halla_tenant_id": HALLA_TENANT}
    assert len(provisioner.sent("PUT", "/integrations/klaros/workforce")) == 1


async def test_pressing_it_again_never_re_issues_credentials(client, provisioner) -> None:
    token = await _ready_mt(client, "again-prov@example.com")
    assert (await client.post(f"{BASE}/workforce/halla/provision", headers=_h(token))).status_code == 200
    again = await client.post(f"{BASE}/workforce/halla/provision", headers=_h(token))
    assert again.status_code == 200 and again.json()["status"] == "CONNECTED"
    assert len(provisioner.calls) == 1  # the second press asked Halla for nothing


async def test_it_is_not_available_without_the_platform_secret_and_says_so(client, provisioner) -> None:
    from app.core.config import get_settings

    token = await _ready_mt(client, "nosecret-prov@example.com")
    s = get_settings()
    s.HALLA_PROVISIONING_SECRET = None
    r = await client.post(f"{BASE}/workforce/halla/provision", headers=_h(token))
    assert r.status_code == 404 and "not available" in r.json()["detail"]
    assert provisioner.calls == []
    setup = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
    assert setup["halla"]["provisioning_available"] is False


async def test_the_status_says_when_automatic_setup_is_available(client, provisioner) -> None:
    token = await _ready_mt(client, "avail-prov@example.com")
    setup = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()
    assert setup["halla"]["provisioning_available"] is True


async def test_a_business_that_has_not_been_set_up_gets_a_plain_refusal_and_halla_is_not_asked(client, provisioner) -> None:
    token = await _reg(client, "Bare Co", "bare-prov@example.com")
    r = await client.post(f"{BASE}/workforce/halla/provision", headers=_h(token))
    assert r.status_code == 422 and "Set up your business first" in r.json()["detail"]
    assert provisioner.calls == []


@pytest.mark.parametrize("reply,code", [
    (httpx.Response(401, json={"success": False}), 502),
    (httpx.Response(404), 502),
    (httpx.Response(503, json={}), 502),
    (httpx.Response(200, json={"success": True, "data": {"tenantId": HALLA_TENANT}}), 502),  # no credentials returned
    (httpx.Response(200, json={"success": True, "data": {"tenantId": HALLA_TENANT, "apiKey": "short", "signingSecret": "short"}}), 422),  # unusable credentials
])
async def test_a_failed_or_unusable_answer_stores_nothing_and_is_never_called_connected(client, provisioner, reply, code) -> None:
    token = await _ready_mt(client, f"fail-prov-{code}-{uuid.uuid4().hex[:4]}@example.com")
    provisioner.reply = reply
    r = await client.post(f"{BASE}/workforce/halla/provision", headers=_h(token))
    assert r.status_code == code, r.text
    info = (await client.get(f"{BASE}/workforce/setup", headers=_h(token))).json()["halla"]
    assert info["has_credential"] is False and info["has_signing_secret"] is False
    assert PLATFORM_SECRET not in r.text


async def test_provisioning_needs_the_manage_integrations_permission(client, provisioner) -> None:
    from tests.test_business_journey_api import _tech_token

    token = await _ready_mt(client, "perm-prov@example.com")
    tech = await _tech_token(client, token, "tech-prov@example.com")
    r = await client.post(f"{BASE}/workforce/halla/provision", headers=_h(tech))
    assert r.status_code in (401, 403)
    assert provisioner.calls == []
