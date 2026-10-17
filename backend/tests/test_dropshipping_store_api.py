"""Dropshipping store, branch 4: the public storefront API (gating, catalog, checkout, order page) and the staff store settings. Stripe is mocked at the HTTP
transport, never called."""

import json
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.dropshipping import Order
from app.models.rbac import Role
from tests.test_dropshipping_api import _h, _tenant_id, _user_token
from tests.test_dropshipping_orders_api import BASE, SHIP, WHO, _owner_with_store

STORE = "/api/v1/public/store"
FRONT = "https://shop.example.test"


class FakeStripe:
    """Records every Checkout Session request and answers like Stripe (or fails on demand)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail = False

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.requests.append({"form": form, "idempotency_key": request.headers.get("idempotency-key")})
            if self.fail:
                return httpx.Response(400, request=request, json={"error": {"message": "SECRET-STRIPE-DETAIL card_declined internals", "type": "invalid_request_error"}})
            n = len(self.requests)
            return httpx.Response(200, request=request, json={"id": f"cs_test_{n}", "url": f"https://checkout.stripe.test/pay/cs_test_{n}", "payment_intent": "pi_fake"})
        return httpx.MockTransport(handler)


@pytest.fixture
def stripe(monkeypatch) -> FakeStripe:
    import app.integrations.stripe_client as mod

    fake = FakeStripe()
    real = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = fake.transport()
        return real(*args, **kwargs)

    settings = get_settings()
    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", FRONT)
    return fake


async def _configure(client, monkeypatch, owner: str) -> uuid.UUID:
    tenant_id = await _tenant_id(client, owner)
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_STORE_TENANT_ID", str(tenant_id))
    return tenant_id


async def _open_store(client, owner: str, **over) -> None:
    body = {"shipping_flat_fee": "5.00", "free_shipping_over": "50.00", "sales_tax_acknowledged": True, "open": True, **over}
    r = await client.put(f"{BASE}/store-settings", json=body, headers=_h(owner))
    assert r.status_code == 200, r.text
    assert r.json()["open_to_shoppers"] is True


async def _live_store(client, monkeypatch, org: str, email: str):
    owner = await _owner_with_store(client, org, email)
    tenant_id = await _configure(client, monkeypatch, owner)
    await _open_store(client, owner)
    return owner, tenant_id


def _shop_body(key: str | None = None, **over) -> dict:
    return {"lines": [{"sku": "TEA-1", "quantity": 2}, {"sku": "RICE-1", "quantity": 1}], "contact": WHO, "ship_to": SHIP, "idempotency_key": key or "web-" + uuid.uuid4().hex, **over}


async def _checkout(client, **over) -> dict:
    r = await client.post(f"{STORE}/checkout", json=_shop_body(**over))
    assert r.status_code == 200, r.text
    return r.json()


async def _order_row(order_number: str) -> Order:
    async with async_session_maker() as session:
        return (await session.execute(select(Order).where(Order.order_number == order_number))).scalar_one()


# --- the store is closed unless everything is in place ---------------------------------------------------------------------------------------------------


async def test_the_public_store_does_not_exist_until_it_is_configured_and_opened(client, monkeypatch, stripe) -> None:
    owner = await _owner_with_store(client, "Closed Store Co", "closed-store@example.com")
    paths = [("get", ""), ("get", "/products"), ("get", "/products/green-tea"), ("post", "/checkout")]

    async def everything_404() -> None:
        for method, path in paths:
            kwargs = {"json": _shop_body()} if method == "post" else {}
            resp = await getattr(client, method)(f"{STORE}{path}", **kwargs)
            assert resp.status_code == 404, (method, path, resp.text)
        assert (await client.get(f"{STORE}/orders/{'x' * 40}")).status_code == 404

    await everything_404()                       # no store tenant configured at all
    await _configure(client, monkeypatch, owner)
    await everything_404()                       # configured but still switched off
    await _open_store(client, owner)
    assert (await client.get(f"{STORE}")).status_code == 200


async def test_only_the_configured_tenant_is_ever_served(client, monkeypatch, stripe) -> None:
    owner_a, _ = await _live_store(client, monkeypatch, "Store A Co", "store-a@example.com")
    owner_b = await _owner_with_store(client, "Store B Co", "store-b@example.com")
    await _open_store(client, owner_b)
    products = (await client.get(f"{STORE}/products")).json()["products"]
    assert {p["slug"] for p in products} == {"green-tea", "brown-rice"}
    # store B is fully open too, yet nothing from it can be reached: the public routes take no tenant from the caller
    b_tenant = await _tenant_id(client, owner_b)
    assert (await client.get(f"{STORE}/products", params={"tenant_id": str(b_tenant)})).status_code == 200
    created = await _checkout(client)
    async with async_session_maker() as session:
        order = (await session.execute(select(Order).where(Order.order_number == created["order_number"]))).scalar_one()
    assert order.tenant_id != b_tenant


@pytest.mark.parametrize("breaks", ["switched_off", "tax_ack_removed", "shipping_removed", "stripe_missing"], ids=lambda v: v)
async def test_each_condition_closes_the_store(client, monkeypatch, stripe, breaks) -> None:
    owner, _ = await _live_store(client, monkeypatch, f"Gate {breaks} Co", f"gate-{breaks}@example.com")
    assert (await client.get(f"{STORE}")).status_code == 200
    if breaks == "stripe_missing":
        monkeypatch.setattr(get_settings(), "STRIPE_SECRET_KEY", None)
    else:
        change = {"switched_off": {"open": False}, "tax_ack_removed": {"sales_tax_acknowledged": False}, "shipping_removed": {"shipping_flat_fee": None, "free_shipping_over": None}}[breaks]
        assert (await client.put(f"{BASE}/store-settings", json=change, headers=_h(owner))).status_code == 200
    assert (await client.get(f"{STORE}")).status_code == 404
    assert (await client.post(f"{STORE}/checkout", json=_shop_body())).status_code == 404
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(Order))).scalar_one() == 0


async def test_store_with_no_published_product_is_closed(client, monkeypatch, stripe) -> None:
    owner = await _register_empty(client, "Empty Store Co", "empty-store@example.com")
    await _configure(client, monkeypatch, owner)
    r = await client.put(f"{BASE}/store-settings", json={"shipping_flat_fee": "5.00", "sales_tax_acknowledged": True, "open": True}, headers=_h(owner))
    assert r.status_code == 422 and any("Publish at least one product" in p for p in r.json()["detail"]["problems"])


async def _register_empty(client, org: str, email: str) -> str:
    from tests.test_business_journey_api import _register

    return await _register(client, org, email)


# --- staff settings --------------------------------------------------------------------------------------------------------------------------------------


async def test_store_settings_permissions_validation_and_audit(client, monkeypatch, stripe) -> None:
    owner = await _owner_with_store(client, "Settings Co", "settings-co@example.com")
    tokens = {role: await _user_token(client, owner, f"{role.value.lower()}@settings.example.com", role) for role in (Role.MANAGER, Role.STAFF, Role.READ_ONLY, Role.TECHNICIAN)}
    # reading: manager, staff, read-only; not technician
    for role in (Role.MANAGER, Role.STAFF, Role.READ_ONLY):
        assert (await client.get(f"{BASE}/store-settings", headers=_h(tokens[role]))).status_code == 200, role
    assert (await client.get(f"{BASE}/store-settings", headers=_h(tokens[Role.TECHNICIAN]))).status_code == 403
    assert (await client.get(f"{BASE}/store-settings")).status_code == 401
    # changing: manager (and owner) only
    for role in (Role.STAFF, Role.READ_ONLY, Role.TECHNICIAN):
        assert (await client.put(f"{BASE}/store-settings", json={"open": False}, headers=_h(tokens[role]))).status_code == 403, role

    view = (await client.get(f"{BASE}/store-settings", headers=_h(owner))).json()
    assert view["open_to_shoppers"] is False and any("shipping rule" in b for b in view["blockers"]) and any("sales tax" in b for b in view["blockers"])

    for bad in ({"shipping_flat_fee": "$5"}, {"shipping_flat_fee": "-1"}, {"open": "yes"}, {"surprise": 1}, {"free_shipping_over": "50.00"}):
        resp = await client.put(f"{BASE}/store-settings", json=bad, headers=_h(owner))
        assert resp.status_code == 422, bad
    # cannot be switched on while something is missing, and the refusal lists what
    resp = await client.put(f"{BASE}/store-settings", json={"open": True}, headers=_h(owner))
    assert resp.status_code == 422 and any("shipping rule" in p for p in resp.json()["detail"]["problems"])

    ok = await client.put(f"{BASE}/store-settings", json={"shipping_flat_fee": "0", "sales_tax_acknowledged": True, "open": True}, headers=_h(tokens[Role.MANAGER]))
    assert ok.status_code == 200 and ok.json()["settings"]["acknowledged_by"]
    tenant_id = await _tenant_id(client, owner)
    async with async_session_maker() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == "dropshipping.store.settings_changed"))).scalars().all()
    assert rows and all("pat" not in json.dumps(r.input_summary) for r in rows)


# --- what a shopper sees ----------------------------------------------------------------------------------------------------------------------------------


async def test_the_catalog_shows_only_what_a_shopper_needs(client, monkeypatch, stripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Catalog Co", "catalog-public@example.com")
    h = _h(owner)
    draft = (await client.post(f"{BASE}/products", json={"name": "Secret Draft", "is_non_perishable": True, "skus": [{"sku_code": "DRAFT-1", "price": "9.00", "supplier": "Acme Supply", "supplier_sku": "DR-S", "supplier_cost": "4.00"}]}, headers=h)).json()["product"]
    assert draft["status"] == "DRAFT"
    sold_out = (await client.post(f"{BASE}/products", json={"name": "Gone Jam", "category": "Pantry", "is_non_perishable": True, "skus": [{"sku_code": "JAM-1", "price": "7.00", "supplier": "Beta Foods", "supplier_sku": "JAM-S", "supplier_cost": "3.00", "availability": "OUT_OF_STOCK"}]}, headers=h)).json()["product"]
    assert (await client.post(f"{BASE}/products/{sold_out['id']}/publish", headers=h)).status_code == 200

    listing = await client.get(f"{STORE}/products")
    assert listing.status_code == 200
    text = listing.text
    for secret in ("Acme Supply", "Beta Foods", "TEA-1-S", "JAM-S", "supplier", "cost", "margin", "Secret Draft", "DRAFT-1"):
        assert secret.lower() not in text.lower(), secret
    by_slug = {p["slug"]: p for p in listing.json()["products"]}
    assert set(by_slug) == {"green-tea", "brown-rice", "gone-jam"}
    assert by_slug["green-tea"]["items"] == [{"sku": "TEA-1", "price": "10.00", "currency": "USD", "in_stock": True}]
    assert by_slug["gone-jam"]["items"][0]["in_stock"] is False

    assert (await client.get(f"{STORE}/products", params={"q": "tea"})).json()["products"][0]["slug"] == "green-tea"
    assert [p["slug"] for p in (await client.get(f"{STORE}/products", params={"category": "pantry"})).json()["products"]] == ["gone-jam"]
    assert (await client.get(f"{STORE}/products/green-tea")).json()["product"]["name"] == "Green Tea"
    assert (await client.get(f"{STORE}/products/secret-draft")).status_code == 404
    info = (await client.get(f"{STORE}")).json()
    assert info["open"] is True and "Sales tax is not collected" in info["sales_tax_notice"] and "$5.00" in info["shipping"]
    assert "acknowledged_by" not in json.dumps(info)


# --- checkout ----------------------------------------------------------------------------------------------------------------------------------------------


async def test_checkout_prices_on_the_server_and_starts_a_stripe_session(client, monkeypatch, stripe) -> None:
    _, tenant_id = await _live_store(client, monkeypatch, "Checkout Co", "checkout-co@example.com")
    out = await _checkout(client)
    assert out["status"] == "PENDING_PAYMENT" and out["total"] == "35.00"           # 2 x 10 + 1 x 10 = 30, under 50 so 5.00 shipping
    assert out["checkout_url"].startswith("https://checkout.stripe.test/pay/") and out["order_number"].startswith("DS-")
    row = await _order_row(out["order_number"])
    assert str(row.shipping) in ("5.00", "5.0") and str(row.tax) in ("0.00", "0.0") and row.stripe_session_id == "cs_test_1" and row.tenant_id == tenant_id

    sent = stripe.requests[0]["form"]
    assert sent["line_items[0][price_data][unit_amount]"] == "3500" and sent["line_items[0][price_data][currency]"] == "usd"
    assert sent["metadata[purpose]"] == "dropshipping_order" and sent["metadata[order_id]"] == str(row.id) and sent["metadata[tenant_id]"] == str(tenant_id)
    assert sent["payment_intent_data[metadata][purpose]"] == "dropshipping_order"
    # both redirect URLs are built on the server from FRONTEND_BASE_URL
    assert sent["success_url"] == f"{FRONT}/store/orders/{row.public_token}?payment=success" and sent["cancel_url"].startswith(f"{FRONT}/store/orders/")


async def test_free_shipping_applies_from_the_threshold(client, monkeypatch, stripe) -> None:
    await _live_store(client, monkeypatch, "Free Ship Co", "free-ship@example.com")
    out = await _checkout(client, lines=[{"sku": "TEA-1", "quantity": 5}])             # 50.00 reaches the threshold
    assert out["total"] == "50.00"
    out = await _checkout(client, lines=[{"sku": "TEA-1", "quantity": 4}])             # 40.00 does not
    assert out["total"] == "45.00"


async def test_a_shopper_cannot_name_a_price_a_shipping_amount_or_a_redirect(client, monkeypatch, stripe) -> None:
    await _live_store(client, monkeypatch, "Tamper Co", "tamper@example.com")
    attempts = [
        _shop_body(shipping="0"), _shop_body(total="1.00"), _shop_body(success_url="https://evil.example/"), _shop_body(tenant_id=str(uuid.uuid4())),
        _shop_body(lines=[{"sku": "TEA-1", "quantity": 1, "price": "0.01"}]), _shop_body(lines=[{"sku": "TEA-1", "quantity": 0}]), _shop_body(lines=[]),
    ]
    for body in attempts:
        assert (await client.post(f"{STORE}/checkout", json=body)).status_code == 422, body
    assert stripe.requests == []


async def test_checkout_is_idempotent_and_keeps_the_price_it_first_gave(client, monkeypatch, stripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Idem Store Co", "idem-store@example.com")
    first = await _checkout(client, key="shopper-key-0001")
    assert (await client.put(f"{BASE}/store-settings", json={"shipping_flat_fee": "9.00"}, headers=_h(owner))).status_code == 200   # the owner changes the rule meanwhile
    second = await _checkout(client, key="shopper-key-0001")
    assert (second["order_number"], second["status_token"], second["total"]) == (first["order_number"], first["status_token"], "35.00")
    assert stripe.requests[0]["idempotency_key"] == stripe.requests[1]["idempotency_key"]
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(Order))).scalar_one() == 1
    other = await client.post(f"{STORE}/checkout", json=_shop_body(key="shopper-key-0001", lines=[{"sku": "TEA-1", "quantity": 9}]))
    assert other.status_code == 409
    assert (await _checkout(client, key="shopper-key-0002"))["total"] == "39.00"   # a new order is priced by the new rule (30 + 9)


async def test_checkout_refuses_what_cannot_be_delivered_or_sold(client, monkeypatch, stripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Refuse Co", "refuse@example.com")
    resp = await client.post(f"{STORE}/checkout", json=_shop_body(ship_to={**SHIP, "state": "HI"}))
    assert resp.status_code == 422 and resp.json()["detail"]["problems"]
    resp = await client.post(f"{STORE}/checkout", json=_shop_body(lines=[{"sku": "NOPE-1", "quantity": 1}]))
    assert resp.status_code == 422 and any("NOPE-1" in p for p in resp.json()["detail"]["problems"])
    sold = (await client.post(f"{BASE}/products", json={"name": "Gone Jam", "is_non_perishable": True, "skus": [{"sku_code": "JAM-1", "price": "7.00", "supplier": "Beta Foods", "supplier_sku": "J", "availability": "OUT_OF_STOCK"}]}, headers=_h(owner))).json()["product"]
    assert (await client.post(f"{BASE}/products/{sold['id']}/publish", headers=_h(owner))).status_code == 200
    resp = await client.post(f"{STORE}/checkout", json=_shop_body(lines=[{"sku": "JAM-1", "quantity": 1}]))
    assert resp.status_code == 422 and any("not available" in p for p in resp.json()["detail"]["problems"])
    assert stripe.requests == []
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(Order))).scalar_one() == 0


async def test_a_stripe_failure_keeps_the_order_and_never_leaks_the_provider_message(client, monkeypatch, stripe) -> None:
    await _live_store(client, monkeypatch, "Stripe Fail Co", "stripe-fail@example.com")
    stripe.fail = True
    resp = await client.post(f"{STORE}/checkout", json=_shop_body(key="retry-key-0001"))
    assert resp.status_code == 502 and "SECRET-STRIPE-DETAIL" not in resp.text and "card_declined" not in resp.text
    async with async_session_maker() as session:
        order = (await session.execute(select(Order))).scalar_one()
    assert order.status == "PENDING_PAYMENT" and order.stripe_session_id is None
    stripe.fail = False
    retry = await _checkout(client, key="retry-key-0001")                                # the same key completes the same order
    assert retry["order_number"] == order.order_number and retry["checkout_url"]


async def test_unpaid_order_cap_stops_order_spam_but_not_a_retry(client, monkeypatch, stripe) -> None:
    await _live_store(client, monkeypatch, "Cap Co", "cap@example.com")
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_MAX_UNPAID_ORDERS", 1)
    first = await _checkout(client, key="cap-key-00001")
    assert (await client.post(f"{STORE}/checkout", json=_shop_body(key="cap-key-00002"))).status_code == 429
    assert (await _checkout(client, key="cap-key-00001"))["order_number"] == first["order_number"]


async def test_public_routes_are_rate_limited(client, monkeypatch, stripe) -> None:
    await _live_store(client, monkeypatch, "Rate Co", "rate@example.com")
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_STORE_BROWSE_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_STORE_CHECKOUT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_STORE_STATUS_PER_MINUTE", 1)
    assert [(await client.get(f"{STORE}/products")).status_code for _ in range(3)] == [200, 200, 429]
    assert (await client.post(f"{STORE}/checkout", json=_shop_body())).status_code == 200
    assert (await client.post(f"{STORE}/checkout", json=_shop_body())).status_code == 429
    assert (await client.get(f"{STORE}/orders/{'y' * 40}")).status_code == 404
    assert (await client.get(f"{STORE}/orders/{'y' * 40}")).status_code == 429


# --- the shopper's order page and paying again ------------------------------------------------------------------------------------------------------


async def test_the_order_page_shows_progress_and_nothing_personal(client, monkeypatch, stripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Status Co", "status-co@example.com")
    out = await _checkout(client)
    page = await client.get(f"{STORE}/orders/{out['status_token']}")
    assert page.status_code == 200
    order = page.json()["order"]
    assert order["order_number"] == out["order_number"] and order["status"] == "PENDING_PAYMENT" and order["can_pay"] is True and order["total"] == "35.00"
    assert [i["name"] for i in order["items"]] == ["Green Tea", "Brown Rice"] and order["tracking"] == []
    for private in ("pat@example.com", "512-555", "12 Main St", "Austin", "78701", "Acme Supply", "Beta Foods", "payment_reference", "supplier", "ship_to", "contact"):
        assert private.lower() not in page.text.lower(), private
    assert (await client.get(f"{STORE}/orders/not-a-real-token-at-all-0000000000")).status_code == 404


async def test_a_token_from_another_business_never_resolves(client, monkeypatch, stripe) -> None:
    await _live_store(client, monkeypatch, "Mine Co", "mine@example.com")
    other = await _owner_with_store(client, "Other Co", "other@example.com")
    theirs = (await client.post(f"{BASE}/orders", json={**_shop_body(), "idempotency_key": "other-key-0001"}, headers=_h(other))).json()["order"]
    async with async_session_maker() as session:
        token = (await session.execute(select(Order.public_token).where(Order.order_number == theirs["order_number"]))).scalar_one()
    assert (await client.get(f"{STORE}/orders/{token}")).status_code == 404
    assert (await client.post(f"{STORE}/orders/{token}/pay")).status_code == 404


async def test_erasing_an_order_kills_its_link(client, monkeypatch, stripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Erase Store Co", "erase-store@example.com")
    out = await _checkout(client)
    oid = (await _order_row(out["order_number"])).id
    assert (await client.post(f"{BASE}/orders/{oid}/cancel", json={"reason": "shopper changed their mind"}, headers=_h(owner))).status_code == 200
    assert (await client.get(f"{STORE}/orders/{out['status_token']}")).json()["order"]["status"] == "CANCELLED"
    assert (await client.post(f"{BASE}/orders/{oid}/erase-personal-data", headers=_h(owner))).status_code == 200
    assert (await client.get(f"{STORE}/orders/{out['status_token']}")).status_code == 404
    assert (await client.post(f"{STORE}/orders/{out['status_token']}/pay")).status_code == 404


async def test_pay_again_gives_a_new_session_only_while_the_order_is_unpaid(client, monkeypatch, stripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Pay Again Co", "pay-again@example.com")
    out = await _checkout(client)
    again = await client.post(f"{STORE}/orders/{out['status_token']}/pay")
    assert again.status_code == 200 and again.json()["checkout_url"] and again.json()["order_number"] == out["order_number"]
    assert stripe.requests[-1]["form"]["line_items[0][price_data][unit_amount]"] == "3500"
    oid = (await _order_row(out["order_number"])).id
    assert (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-77"}, headers=_h(owner))).status_code == 200
    assert (await client.post(f"{STORE}/orders/{out['status_token']}/pay")).status_code == 409
