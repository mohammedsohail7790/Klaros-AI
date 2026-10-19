"""Google Business Profile connector: consent URL, code exchange, refresh-token refusal, verify (ok / not approved / no account), client pagination and error mapping, secrecy.
No network: httpx is mocked at the transport. The connector is read from its module (registration in the shared registry is done by the registry owner)."""

import logging
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import get_settings
from app.integrations import google_business_client as gbc
from app.integrations.connectors import ConnectorCompleteError
from app.integrations.connectors_google_business import CONNECTORS
from app.integrations.google_business_client import GoogleBusinessAPIError, GoogleBusinessClient, GoogleBusinessErrorType

CONNECTOR = CONNECTORS[0]
CB = "https://api.example.com/api/v1/integrations/connectors/google_business_profile/callback"
SECRETS = ("sec-client-secret", "AT-access-secret", "RT-refresh-secret")


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "GOOGLE_BUSINESS_CLIENT_ID", "client-id-1")
    monkeypatch.setattr(s, "GOOGLE_BUSINESS_CLIENT_SECRET", "sec-client-secret")

    async def _nosleep(_):
        return None

    monkeypatch.setattr(gbc.asyncio, "sleep", _nosleep)


_REAL = httpx.AsyncClient


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(gbc.httpx, "AsyncClient", lambda *a, **k: _REAL(*a, **{**k, "transport": httpx.MockTransport(handler)}))


def handler_for(*, token_body=None, token_status=200, accounts=({"name": "accounts/111", "accountName": "Acme", "type": "PERSONAL"},), accounts_status=200, accounts_body=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(token_status, json=token_body if token_body is not None else {"access_token": "AT-access-secret", "refresh_token": "RT-refresh-secret", "expires_in": 3600})
        if request.url.host == "mybusinessaccountmanagement.googleapis.com" and request.url.path == "/v1/accounts":
            if accounts_status != 200:
                return httpx.Response(accounts_status, json=accounts_body or {"error": {"message": "Google My Business API has not been used in project 1 before or it is disabled."}})
            return httpx.Response(200, json={"accounts": list(accounts)})
        return httpx.Response(404, json={})

    return handler


def test_connector_shape_and_owner_steps() -> None:
    assert CONNECTOR.key == "google_business_profile" and CONNECTOR.mode == "oauth" and CONNECTOR.category == "marketing"
    assert CONNECTOR.required_settings == ("GOOGLE_BUSINESS_CLIENT_ID", "GOOGLE_BUSINESS_CLIENT_SECRET")
    assert CONNECTOR.platform_ready()
    text = " ".join(CONNECTOR.owner_steps)
    assert "access request form" in text and "60 days" in text and "business.manage" in text and "GOOGLE_BUSINESS_CLIENT_SECRET" in text


async def test_missing_settings_reported_by_name(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "GOOGLE_BUSINESS_CLIENT_SECRET", None)
    assert CONNECTOR.missing_settings() == ["GOOGLE_BUSINESS_CLIENT_SECRET"]


async def test_start_builds_offline_consent_url() -> None:
    out = await CONNECTOR.start(uuid.uuid4(), uuid.uuid4(), CB, "state-abc")
    url = out["authorization_url"]
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).netloc == "accounts.google.com"
    assert q["client_id"] == ["client-id-1"] and q["redirect_uri"] == [CB] and q["state"] == ["state-abc"]
    assert q["scope"] == ["https://www.googleapis.com/auth/business.manage"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["response_type"] == ["code"]
    assert "sec-client-secret" not in url


async def test_complete_exchanges_code_and_stores_account(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, handler_for(calls=calls))
    result = await CONNECTOR.complete({"code": "the-code"}, CB, uuid.uuid4())
    assert result.credential == {"access_token": "AT-access-secret", "refresh_token": "RT-refresh-secret", "account_name": "accounts/111", "accessible_accounts": ["accounts/111"]}
    assert result.external_account_id == "accounts/111" and result.scopes == "https://www.googleapis.com/auth/business.manage"
    token_call = next(r for r in calls if r.url.host == "oauth2.googleapis.com")
    assert b"code=the-code" in token_call.content and b"authorization_code" in token_call.content
    api_call = next(r for r in calls if r.url.host == "mybusinessaccountmanagement.googleapis.com")
    assert api_call.headers["authorization"] == "Bearer AT-access-secret" and "AT-access-secret" not in str(api_call.url)


async def test_complete_refuses_when_no_refresh_token(monkeypatch) -> None:
    mock_http(monkeypatch, handler_for(token_body={"access_token": "AT-access-secret", "expires_in": 3600}))
    with pytest.raises(ConnectorCompleteError, match="refresh token") as exc:
        await CONNECTOR.complete({"code": "c"}, CB, uuid.uuid4())
    assert not any(s in str(exc.value) for s in SECRETS)


async def test_complete_failure_modes(monkeypatch) -> None:
    mock_http(monkeypatch, handler_for(token_status=400, token_body={"error": "invalid_grant", "error_description": "Bad code"}))
    with pytest.raises(ConnectorCompleteError) as exc:
        await CONNECTOR.complete({"code": "bad"}, CB, uuid.uuid4())
    assert "Bad code" in str(exc.value) and not any(s in str(exc.value) for s in SECRETS)
    mock_http(monkeypatch, handler_for(accounts_status=403))
    with pytest.raises(ConnectorCompleteError, match="not approved"):
        await CONNECTOR.complete({"code": "c"}, CB, uuid.uuid4())
    mock_http(monkeypatch, handler_for(accounts=()))
    with pytest.raises(ConnectorCompleteError, match="no Business Profile account"):
        await CONNECTOR.complete({"code": "c"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="did not grant"):
        await CONNECTOR.complete({"error": "access_denied"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="authorization code"):
        await CONNECTOR.complete({}, CB, uuid.uuid4())


async def test_verify_ok_refreshes_first(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, handler_for(calls=calls))
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret", "account_name": "accounts/111"})
    assert ok and "reachable" in detail and calls[0].url.host == "oauth2.googleapis.com"
    assert not any(s in detail for s in SECRETS)


async def test_verify_not_approved_no_account_missing_token_and_lost_account(monkeypatch) -> None:
    mock_http(monkeypatch, handler_for(accounts_status=403))
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret"})
    assert not ok and "not approved" in detail and "access request" in detail
    mock_http(monkeypatch, handler_for(accounts_status=429, accounts_body={"error": {"message": "Quota exceeded for quota metric 'Requests' ... limit: 0"}}))
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret"})
    assert not ok and "not approved" in detail
    mock_http(monkeypatch, handler_for(accounts=()))
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret"})
    assert not ok and "no Business Profile account" in detail
    mock_http(monkeypatch, handler_for())
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret", "account_name": "accounts/999"})
    assert not ok and "accounts/999" in detail
    ok, detail = await CONNECTOR.verify({})
    assert not ok and "reconnect" in detail
    mock_http(monkeypatch, handler_for(token_status=400, token_body={"error": "invalid_grant", "error_description": "Token revoked"}))
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret"})
    assert not ok and "revoked" in detail.lower()


async def test_client_paginates_locations_and_reviews_and_maps_fields(monkeypatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        q = dict(request.url.params)
        if "businessinformation" in request.url.host:
            if not q.get("pageToken"):
                return httpx.Response(200, json={"locations": [{"name": "locations/1", "title": "Main", "storefrontAddress": {"addressLines": ["1 High St"], "locality": "Leeds", "administrativeArea": "West Yorkshire"}}], "nextPageToken": "p2"})
            return httpx.Response(200, json={"locations": [{"name": "locations/2", "title": "Second"}]})
        if request.url.host == "mybusiness.googleapis.com":
            if not q.get("pageToken"):
                return httpx.Response(200, json={"reviews": [{"reviewId": "r1", "starRating": "FIVE", "comment": "Great", "reviewer": {"displayName": "Ann"}, "createTime": "2026-10-01T10:00:00Z", "updateTime": "2026-10-01T10:00:00Z", "reviewReply": {"comment": "Thanks"}}], "nextPageToken": "n2"})
            return httpx.Response(200, json={"reviews": [{"reviewId": "r2", "starRating": "ONE"}, {"reviewId": "r3", "starRating": "STAR_RATING_UNSPECIFIED"}]})
        return httpx.Response(404, json={})

    mock_http(monkeypatch, handler)
    c = GoogleBusinessClient()
    locs = await c.list_locations(access_token="AT", account_name="accounts/111")
    assert [l.name for l in locs] == ["locations/1", "locations/2"] and locs[0].city == "Leeds" and locs[0].address == "1 High St"
    assert dict(calls[0].url.params)["readMask"] == "name,title,storefrontAddress"
    assert calls[0].url.path == "/v1/accounts/111/locations"
    reviews = await c.list_reviews(access_token="AT", account_name="accounts/111", location_name="locations/1")
    assert [r.review_id for r in reviews] == ["r1", "r2", "r3"] and [r.rating for r in reviews] == [5, 1, None]
    assert reviews[0].reply_comment == "Thanks" and reviews[0].reviewer == "Ann"
    assert calls[-1].url.path == "/v4/accounts/111/locations/1/reviews"


async def test_retries_transient_then_succeeds_and_gives_up_cleanly(monkeypatch) -> None:
    n = {"i": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(503, json={"error": {"message": "backend"}}) if n["i"] < 3 else httpx.Response(200, json={"accounts": []})

    mock_http(monkeypatch, flaky)
    assert await GoogleBusinessClient().list_accounts(access_token="AT") == [] and n["i"] == 3
    mock_http(monkeypatch, lambda r: httpx.Response(500, json={}))
    with pytest.raises(GoogleBusinessAPIError) as exc:
        await GoogleBusinessClient().list_accounts(access_token="AT")
    assert exc.value.error_type == GoogleBusinessErrorType.PROVIDER_ERROR


async def test_no_secrets_in_logs_or_repr(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    mock_http(monkeypatch, handler_for(accounts_status=403))
    ok, detail = await CONNECTOR.verify({"refresh_token": "RT-refresh-secret"})
    assert not ok
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert not any(s in caplog.text for s in SECRETS) and not any(s in repr(GoogleBusinessClient()) for s in SECRETS)
