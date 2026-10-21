"""Dropshipping store: sales tax through Stripe Tax. The pure rules, the settings and the open gate, what checkout sends Stripe, and what the webhook accepts (the tax, and only the
tax). Stripe is mocked at the HTTP transport, never called."""

import json
import re
import uuid
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import get_settings
from app.services import dropshipping_tax as tax
from tests.test_dropshipping_api import _h
from tests.test_dropshipping_orders_api import BASE
from tests.test_dropshipping_store_api import STORE, _checkout, _live_store, _order_row, _shop_body
from tests.test_dropshipping_store_payments import SECRET, _event, _payments, _post, _reload


class TaxStripe:
    """A small Stripe: customers, Checkout Sessions (with the tax it 'calculates'), Tax settings and registrations."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.tax_active = True
        self.registrations: list[dict] = [{"id": "taxreg_1"}]
        self.rate = Decimal("0.0825")
        self.sessions: dict[str, dict] = {}
        self.fail_retrieve = False
        self.report_tax: int | None = None  # force the tax Stripe reports

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            path, method = request.url.path.removeprefix("/v1"), request.method
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.content else {}
            self.requests.append({"method": method, "path": path, "form": form, "idempotency_key": request.headers.get("idempotency-key")})
            ok = lambda body: httpx.Response(200, request=request, json=body)  # noqa: E731
            if path == "/tax/settings":
                return ok({"status": "active" if self.tax_active else "pending"})
            if path == "/tax/registrations":
                return ok({"data": self.registrations})
            if path == "/customers" and method == "POST":
                return ok({"id": f"cus_{len(self.requests)}"})
            if path == "/checkout/sessions" and method == "POST":
                n = len([r for r in self.requests if r["path"] == "/checkout/sessions"])
                sid = f"cs_test_{n}"
                subtotal = sum(int(v) * int(form[k.replace("unit_amount", "quantity").replace("[price_data]", "")]) for k, v in form.items() if k.endswith("[price_data][unit_amount]")) if False else 0
                for k, v in form.items():
                    m = re.match(r"line_items\[(\d+)\]\[price_data\]\[unit_amount\]", k)
                    if m:
                        subtotal += int(v) * int(form[f"line_items[{m.group(1)}][quantity]"])
                if not subtotal:
                    subtotal = int(form["line_items[0][price_data][unit_amount]"])
                taxed = form.get("automatic_tax[enabled]") == "true"
                amount_tax = (self.report_tax if self.report_tax is not None else int((Decimal(subtotal) * self.rate).to_integral_value())) if taxed else 0
                self.sessions[sid] = {"id": sid, "payment_intent": f"pi_tax_{n}", "amount_subtotal": subtotal, "amount_total": subtotal + amount_tax, "total_details": {"amount_tax": amount_tax}}
                return ok({"id": sid, "url": f"https://checkout.stripe.test/pay/{sid}", "payment_intent": f"pi_tax_{n}"})
            if path.startswith("/checkout/sessions/") and method == "GET":
                if self.fail_retrieve:
                    return httpx.Response(500, request=request, json={"error": {"message": "stripe is down"}})
                return ok(self.sessions[path.rsplit("/", 1)[1]])
            return httpx.Response(404, request=request, json={"error": {"message": f"unexpected {method} {path}"}})
        return httpx.MockTransport(handler)

    def calls(self, path: str, method: str = "POST") -> list[dict]:
        return [r for r in self.requests if r["path"] == path and r["method"] == method]


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "STRIPE_WEBHOOK_SECRET", SECRET)


@pytest.fixture
def tstripe(monkeypatch) -> TaxStripe:
    import app.integrations.stripe_client as mod

    fake = TaxStripe()
    real = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = fake.transport()
        return real(*args, **kwargs)

    settings = get_settings()
    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://shop.example.test")
    return fake


async def _tax_store(client, monkeypatch, org: str, email: str):
    owner, tenant_id = await _live_store(client, monkeypatch, org, email)
    r = await client.put(f"{BASE}/store-settings", json={"sales_tax_mode": "STRIPE_TAX"}, headers=_h(owner))
    assert r.status_code == 200, r.text
    return owner, tenant_id


# --- the pure rules -------------------------------------------------------------------------------------------------------------------------------------


def test_the_store_uses_the_grocery_code_and_never_the_prepared_food_one() -> None:
    assert tax.FOOD_TAX_CODE == "txcd_40040000" and tax.FOOD_TAX_CODE != "txcd_40060003"   # 40060003 is food for IMMEDIATE consumption
    assert tax.SHIPPING_TAX_CODE == "txcd_92010001"
    assert tax.TAX_CODE_RE.match("txcd_40040000") and not tax.TAX_CODE_RE.match("txcd_4004") and not tax.TAX_CODE_RE.match("40040000")


def test_the_line_items_carry_each_product_and_a_shipping_line_in_cents() -> None:
    items = tax.build_line_items([("Green Tea", Decimal("10.00"), 2), ("Brown Rice", Decimal("4.50"), 1)], Decimal("5.00"), tax.FOOD_TAX_CODE)
    assert items == [
        {"name": "Green Tea", "unit_amount": 1000, "quantity": 2, "tax_code": "txcd_40040000"},
        {"name": "Brown Rice", "unit_amount": 450, "quantity": 1, "tax_code": "txcd_40040000"},
        {"name": "Shipping", "unit_amount": 500, "quantity": 1, "tax_code": "txcd_92010001"},
    ]
    assert tax.build_line_items([("Tea", Decimal("1.00"), 1)], Decimal("0.00"), tax.FOOD_TAX_CODE)[-1]["name"] == "Tea"   # free shipping: no shipping line


def _session(**over) -> dict:
    return {"payment_intent": "pi_1", "amount_subtotal": 2950, "amount_total": 3193, "total_details": {"amount_tax": 243}, **over}


def test_only_tax_stripe_itself_reports_for_this_payment_is_accepted() -> None:
    assert tax.verify_tax(_session(), payment_intent_id="pi_1", received=Decimal("31.93"), pre_tax_total=Decimal("29.50")) == (Decimal("2.43"), None)
    bad = {
        "another payment": (_session(payment_intent="pi_2"), "31.93", "29.50"),
        "wrong amount received": (_session(), "40.00", "29.50"),
        "pre-tax differs from the order": (_session(), "31.93", "20.00"),
        "no tax": (_session(amount_total=2950, total_details={"amount_tax": 0}), "29.50", "29.50"),
        "tax does not add up": (_session(total_details={"amount_tax": 100}), "31.93", "29.50"),
        "implausible tax": (_session(amount_total=5900, total_details={"amount_tax": 2950}), "59.00", "29.50"),
        "no details": ({"payment_intent": "pi_1", "amount_subtotal": 2950, "amount_total": 3193}, "31.93", "29.50"),
    }
    for name, (session, received, pre) in bad.items():
        value, reason = tax.verify_tax(session, payment_intent_id="pi_1", received=Decimal(received), pre_tax_total=Decimal(pre))
        assert value is None and reason, name


# --- the settings and the open gate -------------------------------------------------------------------------------------------------------------------


async def test_collecting_tax_needs_stripe_tax_active_and_a_registration(client, monkeypatch, tstripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Tax Gate Co", "tax-gate@example.com")
    put = lambda body: client.put(f"{BASE}/store-settings", json=body, headers=_h(owner))  # noqa: E731
    tstripe.tax_active = False
    r = await put({"sales_tax_mode": "STRIPE_TAX"})
    assert r.status_code == 422 and any("not active" in p for p in r.json()["detail"]["problems"])
    tstripe.tax_active, tstripe.registrations = True, []
    r = await put({"sales_tax_mode": "STRIPE_TAX"})
    assert r.status_code == 422 and any("no active tax registration" in p for p in r.json()["detail"]["problems"])
    assert (await put({"sales_tax_mode": "SOMETIMES"})).status_code == 422
    assert (await put({"tax_code": "grocery"})).status_code == 422
    tstripe.registrations = [{"id": "taxreg_1"}]
    ok = await put({"sales_tax_mode": "STRIPE_TAX", "tax_code": "txcd_40040000"})
    assert ok.status_code == 200 and ok.json()["settings"]["sales_tax_mode"] == "STRIPE_TAX" and ok.json()["open_to_shoppers"] is True


async def test_the_shop_tells_shoppers_how_tax_works(client, monkeypatch, tstripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Tax Notice Co", "tax-notice@example.com")
    before = (await client.get(STORE)).json()
    assert before["sales_tax_mode"] == "NONE" and before["sales_tax_notice"] == "Sales tax is not collected on these orders."
    assert (await client.put(f"{BASE}/store-settings", json={"sales_tax_mode": "STRIPE_TAX"}, headers=_h(owner))).status_code == 200
    after = (await client.get(STORE)).json()
    assert after["sales_tax_mode"] == "STRIPE_TAX" and after["sales_tax_notice"] == "Sales tax is calculated at checkout from your delivery address."


async def test_without_stripe_tax_the_owner_must_still_acknowledge_that_tax_is_not_collected(client, monkeypatch, tstripe) -> None:
    owner, _ = await _live_store(client, monkeypatch, "Ack Co", "ack-co@example.com")
    r = await client.put(f"{BASE}/store-settings", json={"sales_tax_acknowledged": False}, headers=_h(owner))
    assert r.status_code == 200 and r.json()["open_to_shoppers"] is False and any("Acknowledge" in b for b in r.json()["blockers"])


# --- checkout: what Stripe is sent ---------------------------------------------------------------------------------------------------------------------


async def test_checkout_sends_stripe_each_product_the_shipping_and_the_delivery_address(client, monkeypatch, tstripe) -> None:
    await _tax_store(client, monkeypatch, "Tax Checkout Co", "tax-checkout@example.com")
    out = await _checkout(client)
    customer = tstripe.calls("/customers")[0]["form"]
    assert customer["shipping[address][postal_code]"] == "78701" and customer["shipping[address][state]"] == "TX" and customer["shipping[address][country]"] == "US"
    assert customer["email"] == "pat@example.com"
    form = tstripe.calls("/checkout/sessions")[0]["form"]
    assert form["automatic_tax[enabled]"] == "true" and form["customer"].startswith("cus_") and "customer_email" not in form
    assert form["line_items[0][price_data][product_data][name]"] == "Green Tea" and form["line_items[0][price_data][unit_amount]"] == "1000" and form["line_items[0][quantity]"] == "2"
    assert form["line_items[0][price_data][product_data][tax_code]"] == "txcd_40040000" and form["line_items[0][price_data][tax_behavior]"] == "exclusive"
    assert form["line_items[2][price_data][product_data][name]"] == "Shipping" and form["line_items[2][price_data][product_data][tax_code]"] == "txcd_92010001"
    assert tstripe.calls("/checkout/sessions")[0]["idempotency_key"].endswith("-tax")
    assert out["total"] == "35.00" and out["checkout_url"].startswith("https://checkout.stripe.test/")   # the order total shown before payment is the pre-tax total


# --- the webhook: the tax, and only the tax -------------------------------------------------------------------------------------------------------------


async def _taxed_order(client, monkeypatch, tstripe, org: str, email: str):
    _, tenant_id = await _tax_store(client, monkeypatch, org, email)
    out = await _checkout(client)
    order = await _order_row(out["order_number"])
    return tenant_id, order, tstripe.sessions["cs_test_1"]


async def test_a_payment_that_is_the_total_plus_the_tax_stripe_reports_pays_the_order_and_records_the_tax(client, monkeypatch, tstripe) -> None:
    tenant_id, order, session = await _taxed_order(client, monkeypatch, tstripe, "Taxed Pay Co", "taxed-pay@example.com")
    assert session["amount_subtotal"] == 3500 and session["total_details"]["amount_tax"] == 289          # 35.00 at 8.25%
    resp = await _post(client, _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, session["amount_total"], pi=session["payment_intent"]))
    assert resp.status_code == 200 and resp.json()["status"] == "processed"
    paid = await _reload(order.id)
    assert paid.status == "PAID" and Decimal(str(paid.tax)) == Decimal("2.89") and Decimal(str(paid.total)) == Decimal("37.89") and paid.payment_reference == session["payment_intent"]
    assert len(await _payments(session["payment_intent"])) == 1


async def test_a_redelivered_taxed_payment_changes_nothing(client, monkeypatch, tstripe) -> None:
    tenant_id, order, session = await _taxed_order(client, monkeypatch, tstripe, "Taxed Redeliver Co", "taxed-redeliver@example.com")
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, session["amount_total"], pi=session["payment_intent"], event_id="evt_same")
    assert (await _post(client, payload)).status_code == 200
    again = await _post(client, _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, session["amount_total"], pi=session["payment_intent"], event_id="evt_other"))
    assert again.status_code == 200
    paid = await _reload(order.id)
    assert paid.status == "PAID" and Decimal(str(paid.total)) == Decimal("37.89") and len(await _payments(session["payment_intent"])) == 1


@pytest.mark.parametrize("why", ["no_tax_reported", "wrong_payment", "inflated"])
async def test_a_bigger_payment_stripe_does_not_vouch_for_is_a_mismatch_for_staff(client, monkeypatch, tstripe, why) -> None:
    tenant_id, order, session = await _taxed_order(client, monkeypatch, tstripe, f"Taxed Bad {why} Co", f"taxed-bad-{why}@example.com")
    cents, pi = session["amount_total"], session["payment_intent"]
    if why == "no_tax_reported":
        session.update(amount_total=3500, total_details={"amount_tax": 0})
        cents = 3800                                  # something was charged on top that Stripe cannot explain as tax
    elif why == "wrong_payment":
        pi = "pi_someone_elses"
    elif why == "inflated":
        cents += 900                                  # more than the session says was due
    await _post(client, _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, cents, pi=pi))
    row = await _reload(order.id)
    assert row.status == "PENDING_PAYMENT" and "review and refund" in (row.status_note or "")             # recorded, flagged, not advanced
    assert len(await _payments(pi)) == 1                                                                  # the money is on record


async def test_if_stripe_cannot_be_asked_the_webhook_is_retried_and_then_completes(client, monkeypatch, tstripe) -> None:
    tenant_id, order, session = await _taxed_order(client, monkeypatch, tstripe, "Taxed Retry Co", "taxed-retry@example.com")
    payload = _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, session["amount_total"], pi=session["payment_intent"], event_id="evt_retry")
    tstripe.fail_retrieve = True
    assert (await _post(client, payload)).status_code == 502
    assert (await _reload(order.id)).status == "PENDING_PAYMENT"
    tstripe.fail_retrieve = False
    assert (await _post(client, payload)).status_code == 200
    assert (await _reload(order.id)).status == "PAID"


async def test_with_no_tax_owed_the_payment_is_just_the_total(client, monkeypatch, tstripe) -> None:
    tstripe.report_tax = 0                          # the customer is somewhere the store is not registered: Stripe charges no tax
    tenant_id, order, session = await _taxed_order(client, monkeypatch, tstripe, "No Tax Owed Co", "no-tax-owed@example.com")
    assert session["amount_total"] == 3500
    assert (await _post(client, _event("payment_intent.succeeded", tenant_id, order.id, order.customer_id, 3500, pi=session["payment_intent"]))).status_code == 200
    paid = await _reload(order.id)
    assert paid.status == "PAID" and Decimal(str(paid.tax)) == 0
