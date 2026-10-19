"""Google Ads connector: consent URL, code exchange, refresh-token refusal, verify, client error classification, secrecy. No network: httpx is mocked at the transport."""

import logging
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import get_settings
from app.integrations import google_ads_client as gac
from app.integrations.connectors import ConnectorCompleteError, get_connector
from app.integrations.google_ads_client import GoogleAdsAPIError, GoogleAdsClient, GoogleAdsErrorType, micros_to_money

CB = "https://api.example.com/api/v1/integrations/connectors/google_ads/callback"
SECRETS = ("sec-client-secret", "dev-token-xyz", "AT-access-secret", "RT-refresh-secret")


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "GOOGLE_ADS_CLIENT_ID", "client-id-1")
    monkeypatch.setattr(s, "GOOGLE_ADS_CLIENT_SECRET", "sec-client-secret")
    monkeypatch.setattr(s, "GOOGLE_ADS_DEVELOPER_TOKEN", "dev-token-xyz")

    async def _nosleep(_):
        return None

    monkeypatch.setattr(gac.asyncio, "sleep", _nosleep)


_REAL = httpx.AsyncClient


def mock_http(monkeypatch, handler):
    real = _REAL
    monkeypatch.setattr(gac.httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))


def google_handler(*, token_body=None, token_status=200, customers=("customers/1234567890",), customers_status=200, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(token_status, json=token_body if token_body is not None else {"access_token": "AT-access-secret", "refresh_token": "RT-refresh-secret", "expires_in": 3600})
        if request.url.path.endswith("customers:listAccessibleCustomers"):
            if customers_status != 200:
                return httpx.Response(customers_status, json={"error": {"message": "The developer token is not approved."}})
            return httpx.Response(200, json={"resourceNames": list(customers)})
        return httpx.Response(404, json={})

    return handler


async def test_connector_registered_and_reports_missing_settings(monkeypatch) -> None:
    c = get_connector("google_ads")
    assert c is not None and c.mode == "oauth" and c.category == "marketing"
    assert c.platform_ready()
    monkeypatch.setattr(get_settings(), "GOOGLE_ADS_DEVELOPER_TOKEN", None)
    assert c.missing_settings() == ["GOOGLE_ADS_DEVELOPER_TOKEN"]


async def test_start_builds_offline_consent_url_with_state_and_redirect() -> None:
    out = await get_connector("google_ads").start(uuid.uuid4(), uuid.uuid4(), CB, "state-abc")
    q = parse_qs(urlparse(out["authorization_url"]).query)
    assert urlparse(out["authorization_url"]).netloc == "accounts.google.com"
    assert q["client_id"] == ["client-id-1"] and q["redirect_uri"] == [CB] and q["state"] == ["state-abc"]
    assert q["scope"] == ["https://www.googleapis.com/auth/adwords"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["response_type"] == ["code"]
    assert "sec-client-secret" not in out["authorization_url"]


async def test_complete_exchanges_code_and_stores_customer(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, google_handler(calls=calls))
    result = await get_connector("google_ads").complete({"code": "the-code"}, CB, uuid.uuid4())
    assert result.credential["refresh_token"] == "RT-refresh-secret" and result.credential["access_token"] == "AT-access-secret"
    assert result.credential["customer_id"] == "1234567890" and result.external_account_id == "1234567890"
    assert result.scopes == "https://www.googleapis.com/auth/adwords"
    token_call = next(r for r in calls if r.url.host == "oauth2.googleapis.com")
    assert b"code=the-code" in token_call.content and b"authorization_code" in token_call.content
    ads_call = next(r for r in calls if r.url.host == "googleads.googleapis.com")
    assert ads_call.headers["developer-token"] == "dev-token-xyz" and ads_call.headers["authorization"] == "Bearer AT-access-secret"


async def test_complete_refuses_when_no_refresh_token(monkeypatch) -> None:
    mock_http(monkeypatch, google_handler(token_body={"access_token": "AT-access-secret", "expires_in": 3600}))
    with pytest.raises(ConnectorCompleteError, match="refresh token") as exc:
        await get_connector("google_ads").complete({"code": "c"}, CB, uuid.uuid4())
    assert not any(s in str(exc.value) for s in SECRETS)


async def test_complete_reports_rejected_code_without_secrets(monkeypatch) -> None:
    mock_http(monkeypatch, google_handler(token_status=400, token_body={"error": "invalid_grant", "error_description": "Bad code"}))
    with pytest.raises(ConnectorCompleteError) as exc:
        await get_connector("google_ads").complete({"code": "bad"}, CB, uuid.uuid4())
    assert "Bad code" in str(exc.value) and not any(s in str(exc.value) for s in SECRETS)


async def test_complete_reports_unapproved_developer_token_and_empty_account_and_denied(monkeypatch) -> None:
    mock_http(monkeypatch, google_handler(customers_status=403))
    with pytest.raises(ConnectorCompleteError, match="developer token"):
        await get_connector("google_ads").complete({"code": "c"}, CB, uuid.uuid4())
    mock_http(monkeypatch, google_handler(customers=()))
    with pytest.raises(ConnectorCompleteError, match="no Google Ads account"):
        await get_connector("google_ads").complete({"code": "c"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="did not grant"):
        await get_connector("google_ads").complete({"error": "access_denied"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="authorization code"):
        await get_connector("google_ads").complete({}, CB, uuid.uuid4())


async def test_verify_ok_refreshes_token_first(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, google_handler(calls=calls))
    ok, detail = await get_connector("google_ads").verify({"refresh_token": "RT-refresh-secret", "customer_id": "1234567890"})
    assert ok and "reachable" in detail
    assert calls[0].url.host == "oauth2.googleapis.com"  # refreshed before the API call
    assert not any(s in detail for s in SECRETS)


async def test_verify_errors_are_honest(monkeypatch) -> None:
    verify = get_connector("google_ads").verify
    assert (await verify({"customer_id": "1"}))[0] is False
    mock_http(monkeypatch, google_handler(token_status=400, token_body={"error": "invalid_grant", "error_description": "Token revoked"}))
    ok, detail = await verify({"refresh_token": "RT-refresh-secret", "customer_id": "1"})
    assert not ok and "revoked" in detail and "RT-refresh-secret" not in detail
    mock_http(monkeypatch, google_handler(customers_status=403))
    ok, detail = await verify({"refresh_token": "RT-refresh-secret", "customer_id": "1"})
    assert not ok and "developer token" in detail
    mock_http(monkeypatch, google_handler())
    ok, detail = await verify({"refresh_token": "RT-refresh-secret", "customer_id": "999"})
    assert not ok and "999" in detail


async def test_client_classifies_errors_and_retries(monkeypatch) -> None:
    n = {"c": 0}

    def flaky(request):
        n["c"] += 1
        return httpx.Response(500, json={"error": {"message": "boom"}}) if n["c"] < 3 else httpx.Response(200, json={"resourceNames": ["customers/7"]})

    mock_http(monkeypatch, flaky)
    assert await GoogleAdsClient().list_accessible_customers(access_token="AT") == ["7"] and n["c"] == 3
    mock_http(monkeypatch, lambda r: httpx.Response(401, json={"error": {"message": "expired"}}))
    with pytest.raises(GoogleAdsAPIError) as exc:
        await GoogleAdsClient().list_accessible_customers(access_token="AT")
    assert exc.value.error_type == GoogleAdsErrorType.AUTHENTICATION
    monkeypatch.setattr(get_settings(), "GOOGLE_ADS_DEVELOPER_TOKEN", None)
    with pytest.raises(GoogleAdsAPIError) as exc:
        await GoogleAdsClient().list_accessible_customers(access_token="AT")
    assert exc.value.error_type == GoogleAdsErrorType.NOT_CONFIGURED


async def test_daily_costs_query_headers_paging_and_micros(monkeypatch) -> None:
    import json
    from datetime import date

    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        if "pageToken" not in body:
            return httpx.Response(200, json={"results": [{"campaign": {"id": "11", "name": "Brand", "status": "ENABLED"}, "metrics": {"costMicros": "12345678"}, "segments": {"date": "2026-10-01"}}], "nextPageToken": "p2"})
        return httpx.Response(200, json={"results": [{"campaign": {"id": "12", "name": "Gen", "status": "PAUSED"}, "metrics": {}, "segments": {"date": "2026-10-02"}}]})

    mock_http(monkeypatch, handler)
    rows = await GoogleAdsClient().daily_campaign_costs(access_token="AT", customer_id="123", start=date(2026, 10, 1), end=date(2026, 10, 2), login_customer_id="999")
    assert [(r.campaign_id, str(r.cost)) for r in rows] == [("11", "12.35"), ("12", "0.00")]
    assert seen[0].headers["login-customer-id"] == "999" and seen[0].url.path.endswith("/customers/123/googleAds:search")
    assert "campaign.id" in json.loads(seen[0].content)["query"] and "segments.date BETWEEN '2026-10-01' AND '2026-10-02'" in json.loads(seen[0].content)["query"]
    assert micros_to_money("1500000") == micros_to_money(1500000)


async def test_no_secret_in_logs_or_repr(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    mock_http(monkeypatch, lambda r: httpx.Response(403, json={"error": {"message": "denied"}}))
    with pytest.raises(GoogleAdsAPIError):
        await GoogleAdsClient().list_accessible_customers(access_token="AT-access-secret")
    text = caplog.text + repr(GoogleAdsClient())
    assert not any(s in text for s in SECRETS)
