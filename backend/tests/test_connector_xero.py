"""Xero connector: consent URL, code exchange, token rotation, verify, client error classification, secrecy. No network: httpx is mocked at the transport."""

import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import get_settings
from app.integrations import connectors_xero as cx
from app.integrations import xero_client as xc
from app.integrations.connectors import ConnectorCompleteError
from app.integrations.xero_client import XeroAPIError, XeroClient, XeroErrorType

pytestmark = pytest.mark.asyncio

CB = "https://api.example.com/api/v1/integrations/connectors/xero/callback"
SECRETS = ("xero-client-secret", "AT-access-secret", "RT-refresh-secret", "RT-new-secret", "AT-new-secret")
CONNECTOR = cx.CONNECTORS[0]
TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "XERO_CLIENT_ID", "xero-client-id")
    monkeypatch.setattr(s, "XERO_CLIENT_SECRET", "xero-client-secret")

    async def _nosleep(_):
        return None

    monkeypatch.setattr(xc.asyncio, "sleep", _nosleep)


_REAL = httpx.AsyncClient


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(xc.httpx, "AsyncClient", lambda *a, **k: _REAL(*a, **{**k, "transport": httpx.MockTransport(handler)}))


def xero_handler(*, token_status=200, token_body=None, connections=None, org_status=200, orgs=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "identity.xero.com":
            body = token_body if token_body is not None else {"access_token": "AT-access-secret", "refresh_token": "RT-refresh-secret", "expires_in": 1800}
            return httpx.Response(token_status, json=body)
        if request.url.path == "/connections":
            return httpx.Response(200, json=connections if connections is not None else [{"tenantId": TENANT, "tenantName": "Demo Company (AU)", "tenantType": "ORGANISATION"}])
        if request.url.path.endswith("/Organisation"):
            if org_status != 200:
                return httpx.Response(org_status, json={"Title": "Unauthorized", "Detail": "AuthenticationUnsuccessful"})
            return httpx.Response(200, json={"Organisations": orgs if orgs is not None else [{"Name": "Demo Company (AU)"}]})
        return httpx.Response(404, json={})

    return handler


def _expired() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


def _fresh() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()


async def test_connector_shape_and_missing_settings(monkeypatch) -> None:
    c = CONNECTOR
    assert c.key == "xero" and c.mode == "oauth" and c.category == "finance"
    assert c.platform_ready()
    monkeypatch.setattr(get_settings(), "XERO_CLIENT_SECRET", None)
    assert c.missing_settings() == ["XERO_CLIENT_SECRET"]
    assert any("25" in step for step in c.owner_steps) and any("XERO_CLIENT_ID" in step for step in c.owner_steps)


async def test_start_builds_consent_url_with_read_scopes_only() -> None:
    out = await CONNECTOR.start(uuid.uuid4(), uuid.uuid4(), CB, "state-1")
    url = urlparse(out["authorization_url"])
    q = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://login.xero.com/identity/connect/authorize"
    assert q["client_id"] == ["xero-client-id"] and q["redirect_uri"] == [CB] and q["state"] == ["state-1"] and q["response_type"] == ["code"]
    scopes = q["scope"][0].split()
    assert "offline_access" in scopes and "accounting.contacts.read" in scopes
    assert not any(s.startswith("accounting.") and not s.endswith(".read") for s in scopes)
    assert "xero-client-secret" not in out["authorization_url"]


async def test_start_without_client_id_is_honest(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "XERO_CLIENT_ID", None)
    with pytest.raises(ConnectorCompleteError, match="XERO_CLIENT_ID"):
        await CONNECTOR.start(uuid.uuid4(), uuid.uuid4(), CB, "s")


async def test_complete_exchanges_code_with_basic_auth_and_stores_credential(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, xero_handler(calls=calls, connections=[
        {"tenantId": TENANT, "tenantName": "Demo Company (AU)", "tenantType": "ORGANISATION"},
        {"tenantId": "other-tenant", "tenantName": "Second Co", "tenantType": "ORGANISATION"},
    ]))
    result = await CONNECTOR.complete({"code": "the-code", "state": "x"}, CB, uuid.uuid4())
    token_req = next(r for r in calls if r.url.host == "identity.xero.com")
    expected = base64.b64encode(b"xero-client-id:xero-client-secret").decode()
    assert token_req.headers["authorization"] == f"Basic {expected}"
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"] and form["redirect_uri"] == [CB]
    assert result.external_account_id == TENANT
    cred = result.credential
    assert cred["access_token"] == "AT-access-secret" and cred["refresh_token"] == "RT-refresh-secret"
    assert cred["xero_tenant_id"] == TENANT and cred["tenant_name"] == "Demo Company (AU)" and cred["expires_at"]
    assert [t["id"] for t in cred["accessible_tenants"]] == [TENANT, "other-tenant"]
    conn_req = next(r for r in calls if r.url.path == "/connections")
    assert conn_req.headers["authorization"] == "Bearer AT-access-secret"
    assert all("AT-access-secret" not in str(r.url) and "RT-refresh-secret" not in str(r.url) for r in calls)


async def test_complete_failures_are_honest_and_secret_free(monkeypatch) -> None:
    with pytest.raises(ConnectorCompleteError, match="did not grant"):
        await CONNECTOR.complete({"error": "access_denied"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="authorization code"):
        await CONNECTOR.complete({}, CB, uuid.uuid4())
    mock_http(monkeypatch, xero_handler(token_status=400, token_body={"error": "invalid_grant"}))
    with pytest.raises(ConnectorCompleteError) as exc:
        await CONNECTOR.complete({"code": "bad"}, CB, uuid.uuid4())
    assert not any(s in str(exc.value) for s in SECRETS)
    mock_http(monkeypatch, xero_handler(connections=[]))
    with pytest.raises(ConnectorCompleteError, match="no organisation"):
        await CONNECTOR.complete({"code": "c"}, CB, uuid.uuid4())
    mock_http(monkeypatch, xero_handler(token_body={"access_token": "AT-access-secret"}))
    with pytest.raises(ConnectorCompleteError, match="refresh token"):
        await CONNECTOR.complete({"code": "c"}, CB, uuid.uuid4())


def _cred(**over):
    base = {"access_token": "AT-access-secret", "refresh_token": "RT-refresh-secret", "expires_at": _fresh(), "xero_tenant_id": TENANT, "tenant_name": "Demo"}
    base.update(over)
    return base


async def test_verify_ok_without_refresh_when_token_is_fresh(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, xero_handler(calls=calls))
    cred = _cred()
    ok, detail = await CONNECTOR.verify(cred)
    assert ok and "Demo Company (AU)" in detail
    assert not any(r.url.host == "identity.xero.com" for r in calls)  # no needless refresh: refresh tokens rotate
    org_req = next(r for r in calls if r.url.path.endswith("/Organisation"))
    assert org_req.headers["xero-tenant-id"] == TENANT and org_req.headers["authorization"] == "Bearer AT-access-secret"
    assert cred["refresh_token"] == "RT-refresh-secret"


async def test_verify_refreshes_near_expiry_and_rotates_credential_in_place(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, xero_handler(calls=calls, token_body={"access_token": "AT-new-secret", "refresh_token": "RT-new-secret", "expires_in": 1800}))
    cred = _cred(expires_at=_expired())
    ok, _ = await CONNECTOR.verify(cred)
    assert ok
    assert cred["refresh_token"] == "RT-new-secret" and cred["access_token"] == "AT-new-secret"
    assert not xc.needs_refresh(cred["expires_at"])
    org_req = next(r for r in calls if r.url.path.endswith("/Organisation"))
    assert org_req.headers["authorization"] == "Bearer AT-new-secret"


async def test_verify_revoked_refresh_token_is_honest(monkeypatch) -> None:
    mock_http(monkeypatch, xero_handler(token_status=400, token_body={"error": "invalid_grant"}))
    ok, detail = await CONNECTOR.verify(_cred(expires_at=_expired()))
    assert not ok and "reconnect" in detail.lower() and not any(s in detail for s in SECRETS)


async def test_verify_no_organisation_and_missing_fields(monkeypatch) -> None:
    mock_http(monkeypatch, xero_handler(orgs=[]))
    ok, detail = await CONNECTOR.verify(_cred())
    assert not ok and "no organisation" in detail.lower()
    ok, detail = await CONNECTOR.verify({"access_token": "x"})
    assert not ok and "reconnect" in detail.lower()
    ok, detail = await CONNECTOR.verify(_cred(refresh_token=None, expires_at=_expired()))
    assert not ok and "reconnect" in detail.lower()


async def test_verify_retries_once_with_refresh_when_access_token_rejected_early(monkeypatch) -> None:
    state = {"org": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "identity.xero.com":
            return httpx.Response(200, json={"access_token": "AT-new-secret", "refresh_token": "RT-new-secret", "expires_in": 1800})
        state["org"] += 1
        if request.headers["authorization"] == "Bearer AT-access-secret":
            return httpx.Response(401, json={"Detail": "TokenExpired"})
        return httpx.Response(200, json={"Organisations": [{"Name": "Demo"}]})

    mock_http(monkeypatch, handler)
    cred = _cred()
    ok, _ = await CONNECTOR.verify(cred)
    assert ok and cred["refresh_token"] == "RT-new-secret" and state["org"] == 2


async def test_verify_403_reports_organisation_disconnected(monkeypatch) -> None:
    mock_http(monkeypatch, xero_handler(org_status=403))
    ok, detail = await CONNECTOR.verify(_cred())
    assert not ok and "reconnect" in detail.lower()


async def test_client_retries_transient_errors_then_gives_typed_error(monkeypatch) -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, json={"Detail": "down"})

    mock_http(monkeypatch, handler)
    with pytest.raises(XeroAPIError) as exc:
        await XeroClient().list_connections(access_token="AT-access-secret")
    assert exc.value.error_type == XeroErrorType.PROVIDER_ERROR and attempts["n"] == xc.MAX_RETRIES


async def test_client_rate_limit_and_not_configured(monkeypatch) -> None:
    mock_http(monkeypatch, lambda r: httpx.Response(429, json={"Detail": "slow down"}, headers={"Retry-After": "1"}))
    with pytest.raises(XeroAPIError) as exc:
        await XeroClient().list_connections(access_token="AT-access-secret")
    assert exc.value.error_type == XeroErrorType.RATE_LIMIT
    monkeypatch.setattr(get_settings(), "XERO_CLIENT_ID", None)
    with pytest.raises(XeroAPIError) as exc2:
        await XeroClient().refresh_access_token(refresh_token="x")
    assert exc2.value.error_type == XeroErrorType.NOT_CONFIGURED


async def test_contacts_paginate(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        n = 100 if page == 1 else 30
        rows = [{"ContactID": f"c{page}-{i}", "Name": f"N{page}-{i}", "ContactStatus": "ACTIVE", "IsCustomer": True} for i in range(n)]
        return httpx.Response(200, json={"Contacts": rows})

    mock_http(monkeypatch, handler)
    contacts = await XeroClient().list_contacts(access_token="a", xero_tenant_id=TENANT, max_records=1000)
    assert len(contacts) == 130
    capped = await XeroClient().list_contacts(access_token="a", xero_tenant_id=TENANT, max_records=120)
    assert len(capped) == 120


async def test_no_secret_in_logs_or_repr(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    mock_http(monkeypatch, xero_handler(org_status=500))
    await CONNECTOR.verify(_cred())
    mock_http(monkeypatch, xero_handler(token_status=400, token_body={"error": "invalid_grant"}))
    await CONNECTOR.verify(_cred(expires_at=_expired()))
    text = caplog.text + repr(XeroClient()) + repr(xc.XeroTokens("AT-access-secret", "RT-refresh-secret", 1800))
    assert not any(s in text for s in SECRETS)
    assert logging.getLogger("httpx").level >= logging.WARNING
