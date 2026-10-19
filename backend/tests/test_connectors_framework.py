"""One-click connector framework: listing, honest missing-platform-settings, signed-state callback, link completion. Uses a fake provider; no network."""

import sys
import types
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.integrations import connectors as registry
from app.integrations.connectors import CompleteResult, Connector, ConnectorCompleteError, CredentialField

pytestmark = pytest.mark.asyncio


async def _register(client, org, email) -> str:
    r = await client.post("/api/v1/auth/register", json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"})
    assert r.status_code == 201, r.text
    return r.json()["tokens"]["access_token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def fake(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "KLAROS_PUBLIC_API_URL", "https://api.example.com")
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "k")
    monkeypatch.setattr(settings, "HALLA_PROVISIONING_SECRET", None)

    async def start(tenant_id, user_id, redirect_uri, state):
        return {"authorization_url": f"https://provider.example/auth?redirect_uri={redirect_uri}&state={state}"}

    async def complete(params, redirect_uri, tenant_id):
        if params.get("code") == "bad":
            raise ConnectorCompleteError("provider refused the code")
        return CompleteResult(credential={"access_token": "tok-" + params.get("code", "x")}, external_account_id="acct-1", scopes="read")

    async def verify(cred):
        return (bool(cred.get("access_token")), "verified")

    module = types.ModuleType("fake_connectors_mod")
    module.CONNECTORS = [
        Connector(key="fakeoauth", name="Fake", category="marketing", description="d", mode="oauth", required_settings=("TAVILY_API_KEY",), owner_steps=("one",),
                  start=start, complete=complete, verify=verify),
        Connector(key="fakemissing", name="Missing", category="marketing", description="d", mode="oauth", required_settings=("HALLA_PROVISIONING_SECRET",), start=start, complete=complete, verify=verify),
        Connector(key="fakepaste", name="Paste", category="communication", description="d", mode="credentials", fields=(CredentialField("token", "Token"),), verify=verify),
    ]
    monkeypatch.setitem(sys.modules, "fake_connectors_mod", module)
    monkeypatch.setattr(registry, "_CONTRIBUTORS", ("fake_connectors_mod",))
    service = get_integration_connection_service()
    for c in module.CONNECTORS:
        service.register_verifier(c.key, c.verify)
    yield
    for c in module.CONNECTORS:
        service._verifiers.pop(c.key, None)


async def test_list_reports_missing_settings_by_name_and_never_a_value(client, fake):
    token = await _register(client, "Conn List Co", "owner@connlist.com")
    rows = {r["key"]: r for r in (await client.get("/api/v1/integrations/connectors", headers=_auth(token))).json()}
    assert rows["fakeoauth"]["platform_ready"] is True and rows["fakeoauth"]["status"] == "NOT_CONNECTED"
    assert rows["fakeoauth"]["redirect_uri"] == "https://api.example.com/api/v1/integrations/connectors/fakeoauth/callback"
    assert rows["fakemissing"]["platform_ready"] is False and rows["fakemissing"]["missing_platform_settings"] == ["HALLA_PROVISIONING_SECRET"]
    assert rows["fakepaste"]["fields"][0]["name"] == "token"


async def test_start_refused_with_clear_reason_when_platform_settings_missing(client, fake):
    token = await _register(client, "Conn Missing Co", "owner@connmissing.com")
    r = await client.post("/api/v1/integrations/connectors/fakemissing/start", headers=_auth(token))
    assert r.status_code == 503 and "HALLA_PROVISIONING_SECRET" in r.json()["detail"]


async def test_full_oauth_round_trip_connects_the_right_tenant_and_hides_the_token(client, fake):
    token = await _register(client, "Conn Flow Co", "owner@connflow.com")
    started = (await client.post("/api/v1/integrations/connectors/fakeoauth/start", headers=_auth(token))).json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    cb = await client.get("/api/v1/integrations/connectors/fakeoauth/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert cb.status_code == 302 and "result=connected" in cb.headers["location"] and cb.headers["location"].startswith("https://app.example.com/settings/integrations")
    rows = {r["key"]: r for r in (await client.get("/api/v1/integrations/connectors", headers=_auth(token))).json()}
    assert rows["fakeoauth"]["status"] == "CONNECTED" and rows["fakeoauth"]["external_account_id"] == "acct-1"
    assert "tok-abc" not in (await client.get("/api/v1/integrations/connectors", headers=_auth(token))).text
    other = await _register(client, "Conn Other Co", "owner@connother.com")
    other_rows = {r["key"]: r for r in (await client.get("/api/v1/integrations/connectors", headers=_auth(other))).json()}
    assert other_rows["fakeoauth"]["status"] == "NOT_CONNECTED"


async def test_callback_rejects_forged_state_and_reports_provider_refusal(client, fake):
    token = await _register(client, "Conn Refuse Co", "owner@connrefuse.com")
    assert (await client.get("/api/v1/integrations/connectors/fakeoauth/callback", params={"code": "x", "state": "forged"})).status_code == 400
    started = (await client.post("/api/v1/integrations/connectors/fakeoauth/start", headers=_auth(token))).json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    bad = await client.get("/api/v1/integrations/connectors/fakeoauth/callback", params={"code": "bad", "state": state}, follow_redirects=False)
    assert "result=error" in bad.headers["location"] and "provider+refused" in bad.headers["location"]
    declined = await client.get("/api/v1/integrations/connectors/fakeoauth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "result=error" in declined.headers["location"]


async def test_a_state_minted_for_one_connector_cannot_complete_another(client, fake):
    token = await _register(client, "Conn Swap Co", "owner@connswap.com")
    started = (await client.post("/api/v1/integrations/connectors/fakeoauth/start", headers=_auth(token))).json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    r = await client.get("/api/v1/integrations/connectors/fakemissing/callback", params={"code": "x", "state": state})
    assert r.status_code == 400


async def test_unknown_connector_is_404(client, fake):
    token = await _register(client, "Conn Unknown Co", "owner@connunknown.com")
    assert (await client.post("/api/v1/integrations/connectors/nope/start", headers=_auth(token))).status_code == 404


async def test_builtin_esignature_and_quickbooks_are_listed(client):
    token = await _register(client, "Conn Real Co", "owner@connreal.com")
    rows = {r["key"]: r for r in (await client.get("/api/v1/integrations/connectors", headers=_auth(token))).json()}
    assert rows["esign"]["built_in"] is True and rows["esign"]["status"] == "CONNECTED"
    assert rows["quickbooks"]["start_path"] == "/integrations/quickbooks/authorize"
