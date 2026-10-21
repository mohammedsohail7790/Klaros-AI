"""Dropshipping: "where is my order?" for a customer who is not signed in. Proof-gated, the same answer for every failure, rate limited, no personal data in the answer or the audit log."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.services import dropshipping_order_lookup as lookup_mod
from app.services.dropshipping_catalog_service import DropshippingCatalogService
from app.services.dropshipping_order_lookup import NOT_FOUND, DropshippingOrderLookupService
from app.services.dropshipping_order_service import DropshippingOrderService
from tests.test_business_journey_api import _register
from tests.test_dropshipping_api import BASE, _h, _tenant_id

SHIP = {"name": "Pat Shopper", "line1": "12 Main St", "city": "Austin", "state": "TX", "postal_code": "78701"}
WHO = {"name": "Pat Shopper", "email": "pat@example.com", "phone": "512-555-0147"}
PRIVATE = ("pat@example.com", "Pat Shopper", "12 Main St", "78701", "512", "555-0147", "Austin")


@pytest.fixture
def catalog() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


@pytest.fixture
def orders() -> DropshippingOrderService:
    return DropshippingOrderService(async_session_maker)


@pytest.fixture
def svc() -> DropshippingOrderLookupService:
    return DropshippingOrderLookupService(async_session_maker)


async def _shop(catalog, t) -> None:
    await catalog.create_supplier(t, "Acme Supply")
    product = await catalog.upsert_product(t, {"name": "Green Tea", "is_non_perishable": True, "skus": [{"sku_code": "TEA-1", "price": "10.00", "supplier": "Acme Supply", "supplier_sku": "TEA-1", "availability": "AVAILABLE"}]})
    await catalog.publish_product(t, uuid.UUID(product["id"]))


async def _order(orders, t, *, pay: bool = True) -> dict:
    o, _ = await orders.create_order(t, lines=[{"sku": "TEA-1", "quantity": 2}], contact=WHO, ship_to=SHIP, idempotency_key="idem-" + uuid.uuid4().hex, shipping="5.00", actor_id=uuid.uuid4())
    return await orders.mark_paid(t, uuid.UUID(o["id"]), payment_reference="STRIPE-SECRET-REF", source="STRIPE") if pay else o


async def test_the_right_number_with_the_right_email_or_phone_gets_the_status(catalog, orders, svc) -> None:
    t = uuid.uuid4()
    await _shop(catalog, t)
    order = await _order(orders, t)
    for proof in ({"email": "PAT@example.com "}, {"phone": "+1 (512) 555-0147"}, {"phone": "5125550147"}, {"email": "pat@example.com", "phone": "512.555.0147"}):
        got = await svc.lookup(t, order_number=order["order_number"].lower(), **proof)
        assert got["found"] is True, proof
        view = got["order"]
        assert view["status"] == "PAID" and view["items"] == [{"name": "Green Tea", "quantity": 2}] and view["total"] == "25.00"
        assert view["summary"] == f"Order {order['order_number']}: Paid. We are preparing your order."


async def test_nothing_personal_is_in_the_answer(catalog, orders, svc) -> None:
    t = uuid.uuid4()
    await _shop(catalog, t)
    order = await _order(orders, t)
    text = json.dumps(await svc.lookup(t, order_number=order["order_number"], email="pat@example.com"))
    for private in PRIVATE + ("STRIPE-SECRET-REF", "Acme Supply", "payment_reference", "contact", "ship_to", "supplier"):
        assert private not in text, private


async def test_every_failure_gets_the_same_answer(catalog, orders, svc) -> None:
    t, other = uuid.uuid4(), uuid.uuid4()
    await _shop(catalog, t)
    await _shop(catalog, other)
    order = await _order(orders, t)
    number = order["order_number"]
    wrong = [
        {"order_number": number, "email": "someone-else@example.com"},         # right order, wrong proof
        {"order_number": number, "phone": "512-555-9999"},
        {"order_number": number, "email": "pat@example.com", "phone": "512-555-9999"},  # both given: both must match
        {"order_number": "DS-NOSUCH99", "email": "pat@example.com"},            # no such order
        {"order_number": number},                                               # no proof at all
        {"order_number": number, "email": "not an email"},
        {"order_number": "", "email": "pat@example.com"},
    ]
    for body in wrong:
        assert await svc.lookup(t, **body) == NOT_FOUND, body
    assert await svc.lookup(other, order_number=number, email="pat@example.com") == NOT_FOUND  # another business cannot see it


async def test_an_erased_order_is_never_revealed(catalog, orders, svc) -> None:
    t = uuid.uuid4()
    await _shop(catalog, t)
    order = await _order(orders, t)
    await orders.cancel_order(t, uuid.UUID(order["id"]), reason="customer asked")
    await orders.erase_personal_data(t, uuid.UUID(order["id"]))
    assert await svc.lookup(t, order_number=order["order_number"], email="pat@example.com") == NOT_FOUND


async def test_repeated_wrong_guesses_lock_that_order_for_an_hour_and_not_others(catalog, orders, svc) -> None:
    t = uuid.uuid4()
    await _shop(catalog, t)
    a, b = await _order(orders, t), await _order(orders, t)
    for i in range(lookup_mod.MAX_FAILS_PER_ORDER):
        assert await svc.lookup(t, order_number=a["order_number"], email=f"guess{i}@example.com") == NOT_FOUND
    assert await svc.lookup(t, order_number=a["order_number"], email="pat@example.com") == NOT_FOUND   # even the right proof is refused now
    assert (await svc.lookup(t, order_number=b["order_number"], email="pat@example.com"))["found"] is True  # another order is unaffected


async def test_a_store_wide_burst_of_guessing_is_stopped(catalog, orders, svc, monkeypatch) -> None:
    monkeypatch.setattr(lookup_mod, "MAX_FAILS_PER_STORE", 3)
    t = uuid.uuid4()
    await _shop(catalog, t)
    order = await _order(orders, t)
    for i in range(3):
        await svc.lookup(t, order_number=f"DS-GUESS{i:03d}", email="x@example.com")
    assert await svc.lookup(t, order_number=order["order_number"], email="pat@example.com") == NOT_FOUND


async def test_a_shipped_order_reads_out_its_tracking(catalog, orders, svc) -> None:
    t = uuid.uuid4()
    await _shop(catalog, t)
    paid = await _order(orders, t)
    so = uuid.UUID(paid["supplier_orders"][0]["id"])
    await orders.mark_supplier_order(t, so, "placed", supplier_reference="R-1")
    await orders.add_shipment(t, so, carrier="UPS", tracking_number="1Z999AA10123456784", tracking_url="https://track.example/1Z")
    got = (await svc.lookup(t, order_number=paid["order_number"], phone="5125550147"))["order"]
    assert got["tracking"] == [{"carrier": "UPS", "tracking_number": "1Z999AA10123456784", "tracking_url": "https://track.example/1Z", "status": "SHIPPED"}]
    assert got["summary"] == f"Order {paid['order_number']} is shipped. It is on its way with UPS, tracking number 1Z999AA10123456784."


async def test_the_route_answers_and_the_proof_is_not_kept_in_the_audit_log(client, catalog, orders) -> None:
    owner = await _register(client, "Lookup Shop", "lookup-owner@example.com")
    t = await _tenant_id(client, owner)
    await _shop(catalog, t)
    order = await _order(orders, t)
    assert (await client.post(f"{BASE}/orders/lookup", json={"order_number": order["order_number"], "email": "pat@example.com"})).status_code == 401
    ok = await client.post(f"{BASE}/orders/lookup", json={"order_number": order["order_number"], "email": "pat@example.com"}, headers=_h(owner))
    assert ok.status_code == 200 and ok.json()["found"] is True and "pat@example.com" not in ok.text
    miss = await client.post(f"{BASE}/orders/lookup", json={"order_number": order["order_number"], "email": "nope@example.com"}, headers=_h(owner))
    assert miss.status_code == 200 and miss.json() == {**NOT_FOUND, "order": None}
    async with async_session_maker() as session:
        rows = list((await session.execute(select(AuditLog.input_summary).where(AuditLog.tenant_id == t, AuditLog.action.like("%lookup_order%")))).scalars())
    assert rows, "the tool call itself is audited"
    blob = json.dumps(rows)
    assert "pat@example.com" not in blob and "nope@example.com" not in blob and order["order_number"] in blob
