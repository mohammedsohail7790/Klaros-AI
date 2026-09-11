"""Phase 16: regression coverage for the public deposit-checkout endpoint's
edge cases surfaced while building the customer-facing UX on top of the
already-verified Phase 15 backend — an already-CONVERTED/DEPOSIT_PENDING
quote's checkout endpoint under a garbage/cross-tenant token, and proof
that the checkout amount is always the server-frozen deposit_amount,
never influenced by anything the browser sends in the request body.
"""

import uuid
from decimal import Decimal

import httpx
import pytest

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.quote import Quote
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_ITEMS = [{"description": "Bathroom remodel", "quantity": "1", "unit_price": "800.00"}]


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_customer(tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext) -> str:
    result = await tool_registry.execute(
        "crm.create_customer", {"name": "Public UX Test Customer", "email": "public-ux-customer@example.com"}, ctx
    )
    return result.customer["id"]


async def _create_and_send_quote(
    tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext, *, deposit_type: str | None = None,
    deposit_value: str | None = None,
) -> tuple[str, str]:
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    body: dict = {"customer_id": customer_id, "line_items": _ITEMS}
    if deposit_type is not None:
        body["deposit_type"] = deposit_type
        body["deposit_value"] = deposit_value
    created = await tool_registry.execute("quotes.create_quote_draft", body, ctx)
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    token = sent.view_url_path.split("token=")[1]
    return created.quote["id"], token


def _stripe_transport(url: str = "https://checkout.stripe.com/pay/cs_test"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"id": "cs_test", "url": url})
    return httpx.MockTransport(handler)


def _patch_stripe_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    import app.integrations.stripe_client as mod

    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)


@pytest.fixture(autouse=True)
def _configure_stripe_key(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    yield


# --- Deposit checkout endpoint: token security. ---


async def test_deposit_checkout_rejects_garbage_token(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, _token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="50.00"
    )
    resp = await client.post(
        f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": "not-a-real-token"}
    )
    assert resp.status_code == 400


async def test_deposit_checkout_rejects_token_from_other_quote(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    _quote_a, token_a = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="50.00"
    )
    quote_b, _token_b = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="50.00"
    )
    # Quote A's real, validly-signed token used against Quote B's id.
    resp = await client.post(f"/api/v1/public/quotes/{quote_b}/deposit/checkout", params={"token": token_a})
    assert resp.status_code == 400


async def test_deposit_checkout_rejects_token_from_other_tenant(client, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)
    _quote_a, token_a = await _create_and_send_quote(
        tool_registry, tenant_a, ctx_a, deposit_type="FIXED", deposit_value="50.00"
    )
    quote_b, _token_b = await _create_and_send_quote(
        tool_registry, tenant_b, ctx_b, deposit_type="FIXED", deposit_value="50.00"
    )
    resp = await client.post(f"/api/v1/public/quotes/{quote_b}/deposit/checkout", params={"token": token_a})
    assert resp.status_code == 400


# --- Deposit checkout: state integrity (already paid / converted / no deposit). ---


async def test_deposit_checkout_on_already_converted_quote_is_rejected(client, tool_registry, monkeypatch) -> None:
    _patch_stripe_transport(monkeypatch, _stripe_transport())
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    # No deposit configured -> accept converts immediately to CONVERTED.
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert resp.status_code == 409


async def test_deposit_checkout_before_accept_is_rejected(client, tool_registry, monkeypatch) -> None:
    _patch_stripe_transport(monkeypatch, _stripe_transport())
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="50.00"
    )
    # Quote is still SENT — customer hasn't accepted yet, so there is no
    # DEPOSIT_PENDING state to check out against.
    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/deposit/checkout", params={"token": token})
    assert resp.status_code == 409


# --- Deposit checkout: amount integrity — the request body has no influence. ---


async def test_deposit_checkout_amount_is_never_taken_from_the_request_body(client, tool_registry, monkeypatch) -> None:
    captured_amounts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        # Stripe's API is form-encoded; the unit_amount field carries cents.
        for part in body.split("&"):
            if "unit_amount" in part:
                captured_amounts.append(part)
        return httpx.Response(200, request=request, json={"id": "cs_test", "url": "https://checkout.stripe.com/pay/cs_test"})

    _patch_stripe_transport(monkeypatch, httpx.MockTransport(handler))
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    # 25% of $800.00 = $200.00 deposit, server-computed at accept time.
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="PERCENTAGE", deposit_value="25"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    # Attacker-style request: a malicious/tampered JSON body attempting to
    # smuggle a different amount. The endpoint takes no body at all, so
    # this must have zero effect on what Stripe is actually charged.
    resp = await client.post(
        f"/api/v1/public/quotes/{quote_id}/deposit/checkout",
        params={"token": token},
        content=b'{"amount": "1.00", "deposit_amount": "0.01"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert len(captured_amounts) == 1
    assert captured_amounts[0].endswith("=20000")  # $200.00 in cents, urlencoded field name

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.deposit_amount == Decimal("200.00")


async def test_deposit_checkout_success_url_never_echoes_client_supplied_url(client, tool_registry, monkeypatch) -> None:
    """The public endpoint deliberately builds success_url/cancel_url
    itself from FRONTEND_BASE_URL — proves a client-supplied redirect
    target has no way to reach Stripe (no open-redirect vector)."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        for part in body.split("&"):
            if part.startswith("success_url=") or part.startswith("cancel_url="):
                key, _, value = part.partition("=")
                captured[key] = value
        return httpx.Response(200, request=request, json={"id": "cs_test", "url": "https://checkout.stripe.com/pay/cs_test"})

    _patch_stripe_transport(monkeypatch, httpx.MockTransport(handler))
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="10.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    resp = await client.post(
        f"/api/v1/public/quotes/{quote_id}/deposit/checkout?evil_redirect=https://attacker.example.com",
        params={"token": token},
    )
    assert resp.status_code == 200
    assert "attacker.example.com" not in captured.get("success_url", "")
    assert "attacker.example.com" not in captured.get("cancel_url", "")


# --- View endpoint: honest, redacted shape. ---


async def test_public_view_never_leaks_deposit_type_or_value(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="PERCENTAGE", deposit_value="30"
    )
    resp = await client.get(f"/api/v1/public/quotes/{quote_id}", params={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deposit_required"] is True
    assert body["deposit_amount"] is None  # not yet frozen — quote hasn't been accepted
    assert "deposit_type" not in body
    assert "deposit_value" not in body
    assert "customer_id" not in body
    assert "tenant_id" not in body


async def test_public_view_shows_frozen_deposit_amount_after_accept(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="60.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    resp = await client.get(f"/api/v1/public/quotes/{quote_id}", params={"token": token})
    body = resp.json()
    assert body["status"] == "DEPOSIT_PENDING"
    assert body["deposit_amount"] == "60.00"
