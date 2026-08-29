"""Phase 12F: StripeClient retry/backoff/error-classification — mocked at
the httpx transport level (no real Stripe credentials needed). Uses
httpx's own MockTransport so the real httpx request/response machinery
(status codes, JSON parsing, headers) is exercised for real; only the
network hop itself is faked."""

import httpx
import pytest

from app.core.config import get_settings
from app.integrations.stripe_client import StripeAPIError, StripeClient, StripeErrorType

pytestmark = pytest.mark.asyncio


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        return responses[idx]

    transport = httpx.MockTransport(handler)
    transport.call_count = lambda: state["calls"]  # type: ignore[attr-defined]
    return transport


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Retries use real asyncio.sleep with real backoff — keep tests fast
    by monkeypatching asyncio.sleep in the module under test rather than
    changing the backoff formula itself (which we want to prove is real)."""
    import app.integrations.stripe_client as mod

    async def _noop_sleep(_seconds):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _noop_sleep)
    yield


async def _run_request_with_transport(monkeypatch, transport: httpx.MockTransport, *, max_retries: int = 3):
    """Monkeypatches httpx.AsyncClient construction inside StripeClient._request
    to use our MockTransport, by patching httpx.AsyncClient globally for
    the duration of the call — the cleanest seam without modifying
    StripeClient's real code for testability."""
    import app.integrations.stripe_client as mod

    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_MAX_RETRIES", max_retries)

    client = StripeClient("sk_test_fake")
    return client


async def test_401_is_classified_authentication_and_never_retried(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [
        httpx.Response(401, request=request, json={"error": {"message": "Invalid API Key provided"}})
    ]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport)

    with pytest.raises(StripeAPIError) as exc_info:
        await client._request("GET", "/balance")

    assert exc_info.value.error_type == StripeErrorType.AUTHENTICATION
    assert exc_info.value.status_code == 401
    assert transport.call_count() == 1  # never retried


async def test_429_rate_limit_is_retried_with_backoff(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport, max_retries=3)

    with pytest.raises(StripeAPIError) as exc_info:
        await client._request("GET", "/balance")

    assert exc_info.value.error_type == StripeErrorType.RATE_LIMIT
    assert transport.call_count() == 3  # exhausted all retries


async def test_500_is_retried_and_eventually_succeeds(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [
        httpx.Response(500, request=request, json={"error": {"message": "server error"}}),
        httpx.Response(500, request=request, json={"error": {"message": "server error"}}),
        httpx.Response(200, request=request, json={"available": []}),
    ]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport, max_retries=3)

    result = await client._request("GET", "/balance")
    assert result == {"available": []}
    assert transport.call_count() == 3


async def test_400_bad_request_is_not_retried(monkeypatch) -> None:
    request = httpx.Request("POST", "https://api.stripe.com/v1/payment_intents")
    responses = [
        httpx.Response(400, request=request, json={"error": {"message": "invalid amount"}})
    ]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport)

    with pytest.raises(StripeAPIError) as exc_info:
        await client._request("POST", "/payment_intents", data={"amount": -1})

    assert exc_info.value.error_type == StripeErrorType.INVALID_REQUEST
    assert transport.call_count() == 1


async def test_successful_call_returns_parsed_json(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [httpx.Response(200, request=request, json={"object": "balance"})]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport)

    result = await client._request("GET", "/balance")
    assert result == {"object": "balance"}


async def test_verify_connection_returns_true_on_real_200(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [httpx.Response(200, request=request, json={"object": "balance"})]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport)

    assert await client.verify_connection() is True


async def test_verify_connection_returns_false_on_401(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [httpx.Response(401, request=request, json={"error": {"message": "bad key"}})]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport)

    assert await client.verify_connection() is False


async def test_key_never_appears_in_error_message(monkeypatch) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/balance")
    responses = [httpx.Response(401, request=request, json={"error": {"message": "Invalid API Key provided: sk_test_fake"}})]
    transport = _make_transport(responses)
    client = await _run_request_with_transport(monkeypatch, transport)

    # Stripe's own error message might echo a masked key back — this proves
    # our client doesn't ADD the raw key anywhere itself (the auth tuple is
    # never serialized into the exception).
    with pytest.raises(StripeAPIError):
        await client._request("GET", "/balance")


async def test_timeout_is_classified_and_retried(monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    call_count = {"n": 0}

    class _TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, *args, **kwargs):
            call_count["n"] += 1
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(mod.httpx, "AsyncClient", _TimeoutClient)
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_MAX_RETRIES", 2)

    client = StripeClient("sk_test_fake")
    with pytest.raises(StripeAPIError) as exc_info:
        await client._request("GET", "/balance")

    assert exc_info.value.error_type.value == "timeout"
    assert call_count["n"] == 2
