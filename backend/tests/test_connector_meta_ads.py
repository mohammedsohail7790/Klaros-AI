"""Meta Ads connector: consent URL, code exchange to a long-lived token, verify (including an expired token), client behaviour, secrecy. No network."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import get_settings
from app.integrations import meta_client as mc
from app.integrations.connectors import ConnectorCompleteError, get_connector
from app.integrations.meta_client import MetaAPIError, MetaClient, MetaErrorType

CB = "https://api.example.com/api/v1/integrations/connectors/meta_ads/callback"
SECRETS = ("meta-app-secret", "SHORT-token-secret", "LONG-token-secret")


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "META_ADS_APP_ID", "app-id-1")
    monkeypatch.setattr(s, "META_ADS_APP_SECRET", "meta-app-secret")

    async def _nosleep(_):
        return None

    monkeypatch.setattr(mc.asyncio, "sleep", _nosleep)


_REAL = httpx.AsyncClient


def mock_http(monkeypatch, handler):
    real = _REAL
    monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))


def meta_handler(*, accounts=None, exchange_status=200, calls=None, account_error=None):
    accounts = [{"id": "act_111", "name": "Acme Ads", "account_status": 1, "currency": "USD"}] if accounts is None else accounts

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        path = request.url.path
        q = dict(request.url.params) | (dict(__import__("urllib.parse").parse.parse_qsl(request.content.decode())) if request.method == "POST" else {})
        if path.endswith("/oauth/access_token"):
            if exchange_status != 200:
                return httpx.Response(exchange_status, json={"error": {"message": "Invalid verification code format."}})
            if q.get("grant_type") == "fb_exchange_token":
                return httpx.Response(200, json={"access_token": "LONG-token-secret", "expires_in": 5184000})
            return httpx.Response(200, json={"access_token": "SHORT-token-secret", "expires_in": 3600})
        if path.endswith("/me/adaccounts"):
            return httpx.Response(200, json={"data": accounts})
        if path.endswith("/act_111"):
            if account_error:
                return httpx.Response(400, json={"error": {"message": account_error}})
            return httpx.Response(200, json={"id": "act_111", "name": "Acme Ads", "account_status": 1})
        return httpx.Response(404, json={})

    return handler


async def test_connector_registered() -> None:
    c = get_connector("meta_ads")
    assert c is not None and c.mode == "oauth" and c.category == "marketing" and c.required_settings == ("META_ADS_APP_ID", "META_ADS_APP_SECRET")


async def test_start_builds_facebook_login_url() -> None:
    out = await get_connector("meta_ads").start(uuid.uuid4(), uuid.uuid4(), CB, "st-1")
    u = urlparse(out["authorization_url"])
    q = parse_qs(u.query)
    assert u.netloc == "www.facebook.com" and u.path == f"/{mc.GRAPH_VERSION}/dialog/oauth"
    assert q["client_id"] == ["app-id-1"] and q["redirect_uri"] == [CB] and q["state"] == ["st-1"]
    assert q["scope"] == ["ads_read,business_management"] and "meta-app-secret" not in out["authorization_url"]


async def test_complete_exchanges_for_long_lived_token_and_picks_active_account(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    accounts = [{"id": "act_999", "name": "Disabled", "account_status": 2}, {"id": "act_111", "name": "Acme Ads", "account_status": 1, "currency": "USD"}]
    mock_http(monkeypatch, meta_handler(accounts=accounts, calls=calls))
    result = await get_connector("meta_ads").complete({"code": "the-code"}, CB, uuid.uuid4())
    assert result.credential["access_token"] == "LONG-token-secret" and result.credential["ad_account_id"] == "act_111"
    assert result.external_account_id == "act_111" and result.scopes == "ads_read,business_management"
    exp = datetime.fromisoformat(result.credential["expires_at"])
    assert timedelta(days=59) < exp - datetime.now(timezone.utc) < timedelta(days=61)
    from urllib.parse import parse_qsl

    exchange = [dict(parse_qsl(r.content.decode())) for r in calls if r.url.path.endswith("/oauth/access_token")]
    assert exchange[0]["code"] == "the-code" and exchange[1]["grant_type"] == "fb_exchange_token" and exchange[1]["fb_exchange_token"] == "SHORT-token-secret"
    assert all(r.method == "POST" and not r.url.query for r in calls if r.url.path.endswith("/oauth/access_token"))  # no credential in a URL
    assert all("access_token=" not in str(r.url) for r in calls) and calls[-1].headers["authorization"] == "Bearer LONG-token-secret"


async def test_complete_failures_are_honest_and_secret_free(monkeypatch) -> None:
    mock_http(monkeypatch, meta_handler(exchange_status=400))
    with pytest.raises(ConnectorCompleteError, match="verification code") as exc:
        await get_connector("meta_ads").complete({"code": "bad"}, CB, uuid.uuid4())
    assert not any(s in str(exc.value) for s in SECRETS)
    mock_http(monkeypatch, meta_handler(accounts=[]))
    with pytest.raises(ConnectorCompleteError, match="no ad account"):
        await get_connector("meta_ads").complete({"code": "c"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="did not grant"):
        await get_connector("meta_ads").complete({"error": "access_denied"}, CB, uuid.uuid4())
    with pytest.raises(ConnectorCompleteError, match="authorization code"):
        await get_connector("meta_ads").complete({}, CB, uuid.uuid4())


def _cred(days: float) -> dict:
    return {"access_token": "LONG-token-secret", "ad_account_id": "act_111", "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()}


async def test_verify_ok_and_reports_expired_token_without_calling_meta(monkeypatch) -> None:
    verify = get_connector("meta_ads").verify
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, meta_handler(calls=calls))
    ok, detail = await verify(_cred(30))
    assert ok and "Acme Ads" in detail and "LONG-token-secret" not in detail
    calls.clear()
    ok, detail = await verify(_cred(-1))
    assert not ok and "expired" in detail and calls == []


async def test_verify_surfaces_meta_error_and_missing_fields(monkeypatch) -> None:
    verify = get_connector("meta_ads").verify
    mock_http(monkeypatch, meta_handler(account_error="Error validating access token: Session has expired"))
    ok, detail = await verify(_cred(30))
    assert not ok and "Session has expired" in detail and "LONG-token-secret" not in detail
    assert (await verify({"access_token": "x"}))[0] is False


async def test_expiry_state_windows() -> None:
    assert mc.token_expiry_state(_cred(30)["expires_at"]) == "ok"
    assert mc.token_expiry_state(_cred(3)["expires_at"]) == "expiring"
    assert mc.token_expiry_state(_cred(-1)["expires_at"]) == "expired"
    assert mc.token_expiry_state(None) == "unknown"


async def test_client_insights_paging_and_retry(monkeypatch) -> None:
    from datetime import date

    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(500, json={"error": {"message": "temp"}})
        assert "access_token" not in str(request.url) and request.headers["authorization"] == "Bearer T"
        if "after" not in request.url.params:
            return httpx.Response(200, json={"data": [{"campaign_id": "1", "campaign_name": "A", "spend": "10.456", "date_start": "2026-10-01"}], "paging": {"next": "https://graph.facebook.com/next?after=2&access_token=T"}})
        return httpx.Response(200, json={"data": [{"campaign_id": "2", "campaign_name": "B", "spend": "0", "date_start": "2026-10-01"}]})

    mock_http(monkeypatch, handler)
    rows = await MetaClient().daily_campaign_costs(access_token="T", ad_account_id="act_1", start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert [(r.campaign_id, str(r.cost)) for r in rows] == [("1", "10.46"), ("2", "0.00")]
    first_ok = seen[1].url.params
    assert first_ok["level"] == "campaign" and first_ok["time_increment"] == "1" and "2026-10-01" in first_ok["time_range"]


async def test_client_auth_error_and_not_configured(monkeypatch) -> None:
    mock_http(monkeypatch, lambda r: httpx.Response(400, json={"error": {"message": "Invalid OAuth access token."}}))
    with pytest.raises(MetaAPIError) as exc:
        await MetaClient().list_ad_accounts(access_token="T")
    assert exc.value.error_type == MetaErrorType.AUTHENTICATION
    monkeypatch.setattr(get_settings(), "META_ADS_APP_SECRET", None)
    with pytest.raises(MetaAPIError) as exc:
        await MetaClient().exchange_code(code="c", redirect_uri=CB)
    assert exc.value.error_type == MetaErrorType.NOT_CONFIGURED


async def test_no_secret_in_logs_or_repr(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    mock_http(monkeypatch, lambda r: httpx.Response(403, json={"error": {"message": "denied"}}))
    with pytest.raises(MetaAPIError) as exc:
        await MetaClient().list_ad_accounts(access_token="LONG-token-secret")
    assert not any(s in caplog.text + repr(MetaClient()) + str(exc.value) for s in SECRETS)
