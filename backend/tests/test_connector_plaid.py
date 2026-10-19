"""Plaid connector + client: link token, public-token exchange (the token never leaks), verify, and honest errors. HTTP is mocked; no network."""

import json
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.integrations import plaid_client
from app.integrations.connectors import ConnectorCompleteError, get_connector
from app.integrations.plaid_client import PlaidAPIError, PlaidClient, PlaidErrorType

pytestmark = pytest.mark.asyncio

ACCESS_TOKEN = "access-sandbox-0123456789-secret-token"
PUBLIC_TOKEN = "public-sandbox-abcdef-public-token"
CLIENT_ID, SECRET = "client-id-for-test", "plaid-secret-for-test"


class FakePlaid:
    """MOCKED HTTP: records requests; `routes` maps a path to a dict body, a (status, body) pair, a list of those (consumed in order) or an Exception."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, object] = {}

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(route, Exception):
            raise route
        if route is None:
            return httpx.Response(404, json={"error_code": "NOT_MOCKED", "error_message": request.url.path})
        status, body = route if isinstance(route, tuple) else (200, route)
        return httpx.Response(status, json=body)


@pytest.fixture
def plaid(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "PLAID_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(s, "PLAID_SECRET", SECRET)
    monkeypatch.setattr(s, "PLAID_ENV", "sandbox")
    monkeypatch.setattr(plaid_client, "_BACKOFF_BASE", 0)
    fake = FakePlaid()
    monkeypatch.setattr(plaid_client, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def _err(code, kind="ITEM_ERROR", message="msg"):
    return {"error_type": kind, "error_code": code, "error_message": message, "request_id": "req-1"}


async def test_the_connector_is_registered_as_link_mode_and_reports_missing_platform_settings(monkeypatch) -> None:
    c = get_connector("plaid")
    assert c is not None and c.mode == "link" and c.required_settings == ("PLAID_CLIENT_ID", "PLAID_SECRET")
    s = get_settings()
    monkeypatch.setattr(s, "PLAID_CLIENT_ID", None)
    monkeypatch.setattr(s, "PLAID_SECRET", None)
    assert c.missing_settings() == ["PLAID_CLIENT_ID", "PLAID_SECRET"] and not c.platform_ready()


async def test_start_creates_a_link_token_with_the_right_request_and_no_personal_data(plaid) -> None:
    plaid.routes["/link/token/create"] = {"link_token": "link-sandbox-xyz", "expiration": "2026-10-19T12:00:00Z"}
    tid, uid = uuid.uuid4(), uuid.uuid4()
    out = await get_connector("plaid").start(tid, uid, "", "state")
    assert out["link_token"] == "link-sandbox-xyz"
    req = plaid.requests[0]
    assert str(req.url) == "https://sandbox.plaid.com/link/token/create"
    assert req.headers["PLAID-CLIENT-ID"] == CLIENT_ID and req.headers["PLAID-SECRET"] == SECRET
    body = json.loads(req.content)
    assert body["client_name"] == "Klaros" and body["products"] == ["transactions"] and body["language"] == "en"
    assert {"US", "CA", "GB"} <= set(body["country_codes"])
    assert body["user"]["client_user_id"] == f"klaros-{tid.hex}-{uid.hex}" and "email" not in body["user"]
    assert CLIENT_ID not in req.content.decode() and SECRET not in req.content.decode()  # credentials travel in headers only


async def test_production_env_uses_the_production_host(plaid, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "PLAID_ENV", "production")
    plaid.routes["/link/token/create"] = {"link_token": "link-production-1"}
    await PlaidClient().create_link_token(client_user_id="x")
    assert plaid.requests[0].url.host == "production.plaid.com"


async def test_exchange_stores_the_token_in_the_credential_and_never_leaks_it(plaid) -> None:
    plaid.routes["/item/public_token/exchange"] = {"access_token": ACCESS_TOKEN, "item_id": "item-123", "request_id": "r"}
    result = await get_connector("plaid").complete({"public_token": PUBLIC_TOKEN, "metadata": {"institution": {"name": "Test Bank"}}}, "", uuid.uuid4())
    assert result.credential == {"access_token": ACCESS_TOKEN, "item_id": "item-123"} and result.external_account_id == "item-123"
    assert ACCESS_TOKEN not in repr(result.external_account_id) and ACCESS_TOKEN not in str(result.scopes)
    assert json.loads(plaid.requests[0].content) == {"public_token": PUBLIC_TOKEN}


async def test_a_refused_exchange_gives_an_honest_message_without_any_token(plaid) -> None:
    plaid.routes["/item/public_token/exchange"] = (400, _err("INVALID_PUBLIC_TOKEN", "INVALID_INPUT", "provided public token is expired"))
    with pytest.raises(ConnectorCompleteError) as caught:
        await get_connector("plaid").complete({"public_token": PUBLIC_TOKEN}, "", uuid.uuid4())
    text = str(caught.value)
    assert "INVALID_PUBLIC_TOKEN" in text and PUBLIC_TOKEN not in text and SECRET not in text and CLIENT_ID not in text
    with pytest.raises(ConnectorCompleteError):
        await get_connector("plaid").complete({}, "", uuid.uuid4())  # nothing posted by the browser


async def test_verify_ok_makes_a_real_read_only_call(plaid) -> None:
    plaid.routes["/item/get"] = {"item": {"item_id": "item-123", "institution_id": "ins_1", "error": None}}
    ok, detail = await get_connector("plaid").verify({"access_token": ACCESS_TOKEN, "item_id": "item-123"})
    assert ok and "/item/get" in detail and ACCESS_TOKEN not in detail
    assert plaid.bodies("/item/get") == [{"access_token": ACCESS_TOKEN}]


async def test_verify_reports_login_required_whether_plaid_says_it_in_the_body_or_as_an_error(plaid) -> None:
    plaid.routes["/item/get"] = {"item": {"item_id": "i", "error": _err("ITEM_LOGIN_REQUIRED")}}
    ok, detail = await get_connector("plaid").verify({"access_token": ACCESS_TOKEN})
    assert not ok and "ITEM_LOGIN_REQUIRED" in detail
    plaid.routes["/item/get"] = (400, _err("ITEM_LOGIN_REQUIRED"))
    ok, detail = await get_connector("plaid").verify({"access_token": ACCESS_TOKEN})
    assert not ok and "ITEM_LOGIN_REQUIRED" in detail and ACCESS_TOKEN not in detail
    assert await get_connector("plaid").verify({}) == (False, "No Plaid access token is stored")


async def test_bad_platform_keys_are_an_authentication_error(plaid) -> None:
    plaid.routes["/item/get"] = (400, _err("INVALID_API_KEYS", "INVALID_INPUT", "invalid client_id or secret"))
    ok, detail = await get_connector("plaid").verify({"access_token": ACCESS_TOKEN})
    assert not ok and "INVALID_API_KEYS" in detail
    with pytest.raises(PlaidAPIError) as caught:
        await PlaidClient().get_item(ACCESS_TOKEN)
    assert caught.value.error_type is PlaidErrorType.AUTHENTICATION and caught.value.error_code == "INVALID_API_KEYS"


async def test_transient_failures_are_retried_a_bounded_number_of_times(plaid) -> None:
    plaid.routes["/item/get"] = [(500, _err("INTERNAL_SERVER_ERROR", "API_ERROR")), (429, _err("RATE_LIMIT", "RATE_LIMIT_EXCEEDED")), {"item": {"item_id": "i"}}]
    assert (await PlaidClient().get_item(ACCESS_TOKEN))["item"]["item_id"] == "i" and len(plaid.requests) == 3
    plaid.requests.clear()
    plaid.routes["/item/get"] = (503, _err("X", "API_ERROR"))
    with pytest.raises(PlaidAPIError) as caught:
        await PlaidClient().get_item(ACCESS_TOKEN)
    assert caught.value.error_type is PlaidErrorType.PROVIDER_ERROR and len(plaid.requests) == plaid_client.MAX_RETRIES


async def test_timeouts_and_network_errors_are_typed_and_leak_nothing(plaid) -> None:
    plaid.routes["/item/get"] = httpx.ReadTimeout(f"timed out for {ACCESS_TOKEN}")
    with pytest.raises(PlaidAPIError) as caught:
        await PlaidClient().get_item(ACCESS_TOKEN)
    assert caught.value.error_type is PlaidErrorType.TIMEOUT and ACCESS_TOKEN not in str(caught.value)
    plaid.routes["/item/get"] = httpx.ConnectError(f"cannot connect {SECRET}")
    with pytest.raises(PlaidAPIError) as caught:
        await PlaidClient().get_item(ACCESS_TOKEN)
    assert caught.value.error_type is PlaidErrorType.NETWORK_ERROR and SECRET not in str(caught.value)


async def test_an_unconfigured_deployment_never_calls_plaid(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "PLAID_CLIENT_ID", None)
    monkeypatch.setattr(s, "PLAID_SECRET", None)
    with pytest.raises(PlaidAPIError) as caught:
        await PlaidClient().get_item(ACCESS_TOKEN)
    assert caught.value.error_type is PlaidErrorType.NOT_CONFIGURED
    assert "PlaidClient(configured=False)" == repr(PlaidClient())
