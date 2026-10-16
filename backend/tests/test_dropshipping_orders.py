"""Dropshipping store, branch 3: the order rules, the order service (create, pay, supplier orders, tracking, delivery, cancel, refund flag, erasure) and the
`search_orders` tool. The staff API over HTTP is covered in tests/test_dropshipping_orders_api.py."""

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.crm import Customer
from app.models.dropshipping import Order, OrderStatus, ShipmentStatus, Sku, SupplierOrder, SupplierOrderStatus
from app.models.rbac import Role
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError, DropshippingCatalogService
from app.services.dropshipping_order_rules import (
    CONTIGUOUS_STATES,
    ERASABLE,
    TRANSITIONS,
    OrderTransitionError,
    check_transition,
    new_order_number,
    normalize_contact,
    normalize_order_lines,
    normalize_reference,
    normalize_ship_to,
    normalize_tracking,
)
from app.services.dropshipping_order_service import DropshippingOrderService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolPermissionError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

SHIP = {"name": "Pat Shopper", "line1": "12 Main St", "city": "Austin", "state": "TX", "postal_code": "78701"}
WHO = {"name": "Pat Shopper", "email": "pat@example.com", "phone": "512-555-0147"}


@pytest.fixture
def catalog() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


@pytest.fixture
def orders() -> DropshippingOrderService:
    return DropshippingOrderService(async_session_maker)


async def _store(catalog: DropshippingCatalogService, tenant: uuid.UUID, *, two_suppliers: bool = False) -> None:
    """A tiny published, in-stock store: Tea (Acme Supply) and Rice (Acme Supply, or Beta Foods when two_suppliers)."""
    await catalog.create_supplier(tenant, "Acme Supply")
    await catalog.create_supplier(tenant, "Beta Foods")
    for name, code, price, supplier, ssku in (("Green Tea", "TEA-1", "10.00", "Acme Supply", "AS-TEA"), ("Brown Rice", "RICE-1", "4.50", "Beta Foods" if two_suppliers else "Acme Supply", "BF-RICE")):
        product = await catalog.upsert_product(tenant, {
            "name": name, "is_non_perishable": True,
            "skus": [{"sku_code": code, "price": price, "supplier": supplier, "supplier_sku": ssku, "availability": "AVAILABLE"}],
        })
        await catalog.publish_product(tenant, uuid.UUID(product["id"]))


def _key() -> str:
    return "idem-" + uuid.uuid4().hex


async def _order(orders, tenant, lines=None, *, key=None, who=None, ship=None, shipping="0") -> dict:
    out, created = await orders.create_order(
        tenant, lines=lines or [{"sku": "TEA-1", "quantity": 2}, {"sku": "RICE-1", "quantity": 1}], contact=who or WHO, ship_to=ship or SHIP,
        idempotency_key=key or _key(), shipping=shipping, actor_id=uuid.uuid4(),
    )
    assert created
    return out


async def _paid(orders, tenant, **kw) -> dict:
    order = await _order(orders, tenant, **kw)
    return await orders.mark_paid(tenant, uuid.UUID(order["id"]), payment_reference="BANK-" + uuid.uuid4().hex[:8], source="MANUAL", actor_id=uuid.uuid4())


async def _actions(tenant: uuid.UUID) -> list[str]:
    async with async_session_maker() as session:
        return [a for (a,) in (await session.execute(select(AuditLog.action).where(AuditLog.tenant_id == tenant))).all()]


# --- the pure rules --------------------------------------------------------------------------------------------------------------------------------------


def test_the_ship_to_rules_follow_the_suppliers_reach() -> None:
    assert normalize_ship_to({**SHIP, "state": "tx", "postal_code": "78701-1234"})["state"] == "TX"
    assert len(CONTIGUOUS_STATES) == 49 and not ({"AK", "HI", "PR", "GU", "VI"} & CONTIGUOUS_STATES)
    for bad in ({"state": "AK"}, {"state": "HI"}, {"state": "PR"}, {"state": ""}, {"postal_code": "7870"}, {"postal_code": "ABCDE"}, {"country": "CA"}, {"line1": ""}, {"name": " "}, {"city": "x" * 81}):
        with pytest.raises(CatalogValidationError):
            normalize_ship_to({**SHIP, **bad})
    with pytest.raises(CatalogValidationError) as info:
        normalize_ship_to({})
    assert len(info.value.problems) >= 4  # every missing field is reported
    with pytest.raises(CatalogValidationError):
        normalize_ship_to("not an address")


def test_contact_lines_tracking_and_reference_rules() -> None:
    assert normalize_contact(WHO)["email"] == "pat@example.com"
    assert normalize_contact({"name": "Pat", "phone": "(512) 555-0147"})["email"] is None
    for bad in ({"name": "Pat"}, {"name": "", "email": "a@b.co"}, {"name": "Pat", "email": "nope"}, {"name": "Pat", "phone": "12345"}):
        with pytest.raises(CatalogValidationError):
            normalize_contact(bad)
    assert normalize_order_lines([{"sku": "A", "quantity": 1}, {"sku_code": "A", "quantity": 2}]) == [("A", 3)]  # the same item twice is merged
    for bad in ([], None, [{"sku": "", "quantity": 1}], [{"sku": "A", "quantity": 0}], [{"sku": "A", "quantity": 100}], [{"sku": "A", "quantity": True}], [{"sku": "A", "quantity": 1.5}],
                [{"sku": "A", "quantity": 60}, {"sku": "A", "quantity": 60}], [{"sku": "A", "quantity": 1}] * 51):
        with pytest.raises(CatalogValidationError):
            normalize_order_lines(bad)
    assert normalize_tracking("UPS", "1Z999AA10123456784", "https://track.example/1Z")[1] == "1Z999AA10123456784"
    for bad in (("UPS", "", None), ("UPS", "x", None), ("UPS", "bad;number", None), ("UPS", "1Z999", "http://insecure.example")):
        with pytest.raises(CatalogValidationError):
            normalize_tracking(*bad)
    assert normalize_reference("  BANK-1  ") == "BANK-1"
    for bad in ("", "   ", None, "x" * 161):
        with pytest.raises(CatalogValidationError):
            normalize_reference(bad)
    assert new_order_number().startswith("DS-") and len(new_order_number()) == 11 and not set(new_order_number()[3:]) & set("01OI")


def test_the_state_machine_allows_only_the_agreed_moves() -> None:
    allowed = {
        ("PENDING_PAYMENT", "PAID"), ("PENDING_PAYMENT", "CANCELLED"), ("PAID", "SUPPLIER_ORDER_PLACED"), ("PAID", "REFUND_REQUESTED"), ("PAID", "CANCELLED"),
        ("SUPPLIER_ORDER_PLACED", "SHIPPED"), ("SUPPLIER_ORDER_PLACED", "REFUND_REQUESTED"), ("SHIPPED", "DELIVERED"), ("SHIPPED", "REFUND_REQUESTED"), ("DELIVERED", "REFUND_REQUESTED"),
    }
    every = [s.value for s in OrderStatus]
    for a in every:
        for b in every:
            if (a, b) in allowed:
                check_transition(a, b)
            else:
                with pytest.raises(OrderTransitionError):
                    check_transition(a, b)
    assert TRANSITIONS["CANCELLED"] == frozenset() and TRANSITIONS["REFUND_REQUESTED"] == frozenset()  # they end the automated flow
    assert {s.value for s in ERASABLE} == {"DELIVERED", "CANCELLED", "REFUND_REQUESTED"}


# --- creating an order -----------------------------------------------------------------------------------------------------------------------------------


async def test_an_order_is_priced_from_the_catalog_and_snapshots_the_price(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    order = await _order(orders, t, shipping="5.00")
    assert order["status"] == "PENDING_PAYMENT" and order["currency"] == "USD"
    assert (order["subtotal"], order["shipping"], order["tax"], order["total"]) == ("24.50", "5.00", "0.00", "29.50")  # tax is recorded explicitly as 0
    assert {(ln["sku_code"], ln["quantity"], ln["unit_price"], ln["line_total"]) for ln in order["lines"]} == {("TEA-1", 2, "10.00", "20.00"), ("RICE-1", 1, "4.50", "4.50")}
    assert order["order_number"].startswith("DS-") and order["ship_to"]["state"] == "TX" and order["ship_to"]["country"] == "US"
    # the shopper's price is a SNAPSHOT: a later catalog price change never alters the order
    await catalog.upsert_product(t, {"name": "Green Tea", "is_non_perishable": True, "skus": [{"sku_code": "TEA-1", "price": "99.00", "supplier": "Acme Supply", "supplier_sku": "AS-TEA", "availability": "AVAILABLE"}]})
    again = await orders.get_order(t, uuid.UUID(order["id"]))
    assert again["total"] == "29.50" and again["lines"][0]["unit_price"] in ("10.00", "4.50")
    assert "dropshipping.order.created" in await _actions(t)


async def test_the_customer_is_matched_by_exact_email_or_phone_never_duplicated_or_overwritten(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    first = await _order(orders, t)
    second = await _order(orders, t, who={"name": "A Different Name", "email": "PAT@example.com"})  # same email, different case and name
    third = await _order(orders, t, who={"name": "Phone Match", "phone": "(512) 555-0147"})  # same phone, different format
    assert first["customer_id"] == second["customer_id"] == third["customer_id"]
    async with async_session_maker() as session:
        customers = list((await session.execute(select(Customer).where(Customer.tenant_id == t))).scalars())
    assert len(customers) == 1 and customers[0].name == "Pat Shopper"  # an existing customer is never overwritten
    other = await _order(orders, t, who={"name": "Someone Else", "email": "else@example.com"})
    assert other["customer_id"] != first["customer_id"]


async def test_an_item_that_is_not_for_sale_is_refused_and_nothing_is_created(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    draft = await catalog.upsert_product(t, {"name": "Draft Item", "is_non_perishable": True, "skus": [{"sku_code": "DRAFT-1", "price": "3.00", "supplier": "Acme Supply", "availability": "AVAILABLE"}]})
    unknown_stock = await catalog.upsert_product(t, {"name": "Maybe Item", "is_non_perishable": True, "skus": [{"sku_code": "MAYBE-1", "price": "3.00", "supplier": "Acme Supply"}]})
    await catalog.publish_product(t, uuid.UUID(unknown_stock["id"]))
    gone = await catalog.upsert_product(t, {"name": "Gone Item", "is_non_perishable": True, "skus": [{"sku_code": "GONE-1", "price": "3.00", "supplier": "Acme Supply", "availability": "OUT_OF_STOCK"}]})
    await catalog.publish_product(t, uuid.UUID(gone["id"]))
    for code, expected in (("DRAFT-1", "not for sale"), ("MAYBE-1", "not available"), ("GONE-1", "not available"), ("NOPE-9", "unknown item")):
        with pytest.raises(CatalogValidationError) as info:
            await orders.create_order(t, lines=[{"sku": "TEA-1", "quantity": 1}, {"sku": code, "quantity": 1}], contact=WHO, ship_to=SHIP, idempotency_key=_key())
        assert expected in " ".join(info.value.problems), code
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(Order).where(Order.tenant_id == t))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(Customer).where(Customer.tenant_id == t))).scalar_one() == 0  # no half-created customer either
    assert draft["status"] == "DRAFT"


async def test_bad_order_input_lists_every_problem(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    with pytest.raises(CatalogValidationError) as info:
        await orders.create_order(t, lines=[], contact={"name": ""}, ship_to={"state": "AK"}, idempotency_key="short", shipping="-1")
    assert len(info.value.problems) >= 5


async def test_a_retried_order_returns_the_same_order_even_if_stock_changed_and_a_different_one_is_a_conflict(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    key = _key()
    first = await _order(orders, t, key=key)
    again, created = await orders.create_order(t, lines=[{"sku": "RICE-1", "quantity": 1}, {"sku": "TEA-1", "quantity": 2}], contact=WHO, ship_to=SHIP, idempotency_key=key)
    assert created is False and again["id"] == first["id"]
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(Order).where(Order.tenant_id == t))).scalar_one() == 1
    sku = (await catalog.search_products(t, q="tea"))[0]["skus"][0]
    await catalog.set_availability(t, uuid.UUID(sku["id"]), "OUT_OF_STOCK", quantity=0)  # stock changes after the shopper ordered
    replay, created = await orders.create_order(t, lines=[{"sku": "TEA-1", "quantity": 2}, {"sku": "RICE-1", "quantity": 1}], contact=WHO, ship_to=SHIP, idempotency_key=key)
    assert created is False and replay["id"] == first["id"]
    for different in ({"lines": [{"sku": "TEA-1", "quantity": 3}]}, {"ship_to": {**SHIP, "city": "Dallas"}}, {"shipping": "9.00"}, {"contact": {**WHO, "email": "other@example.com"}}):
        args = {"lines": [{"sku": "TEA-1", "quantity": 2}, {"sku": "RICE-1", "quantity": 1}], "contact": WHO, "ship_to": SHIP, "shipping": "0", **different}
        with pytest.raises((CatalogConflictError, CatalogValidationError)):
            await orders.create_order(t, idempotency_key=key, **args)


async def test_businesses_never_see_or_touch_each_others_orders(catalog, orders) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await _store(catalog, a)
    await _store(catalog, b)
    order = await _order(orders, a)
    oid = uuid.UUID(order["id"])
    assert await orders.search_orders(b) == []
    for call in (orders.get_order(b, oid), orders.mark_paid(b, oid, payment_reference="X-1", source="MANUAL"), orders.cancel_order(b, oid, reason="x"),
                 orders.erase_personal_data(b, oid), orders.request_refund(b, oid, reason="x")):
        with pytest.raises(CatalogNotFoundError):
            await call
    with pytest.raises(CatalogNotFoundError):
        await orders.mark_supplier_order(b, uuid.uuid4(), "failed", note="x")
    assert (await orders.get_order(a, oid))["status"] == "PENDING_PAYMENT"


# --- payment ------------------------------------------------------------------------------------------------------------------------------------------------


async def test_recording_a_payment_creates_one_supplier_order_per_supplier_and_is_idempotent(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t, two_suppliers=True)
    order = await _order(orders, t)
    oid = uuid.UUID(order["id"])
    for bad in ("", "   ", None):
        with pytest.raises(CatalogValidationError):
            await orders.mark_paid(t, oid, payment_reference=bad, source="MANUAL")
    with pytest.raises(CatalogValidationError):
        await orders.mark_paid(t, oid, payment_reference="X-1", source="CRYPTO")
    paid = await orders.mark_paid(t, oid, payment_reference="BANK-77", source="MANUAL", actor_id=uuid.uuid4())
    assert paid["status"] == "PAID" and paid["paid_at"] and paid["payment_reference"] == "BANK-77" and paid["payment_source"] == "MANUAL"
    assert sorted(so["supplier"] for so in paid["supplier_orders"]) == ["Acme Supply", "Beta Foods"] and {so["status"] for so in paid["supplier_orders"]} == {"TO_PLACE"}
    again = await orders.mark_paid(t, oid, payment_reference="BANK-77", source="MANUAL")
    assert again["status"] == "PAID" and len(again["supplier_orders"]) == 2  # same reference: no second payment, no second set of supplier orders
    with pytest.raises(CatalogConflictError):
        await orders.mark_paid(t, oid, payment_reference="BANK-78", source="MANUAL")
    assert (await _actions(t)).count("dropshipping.order.paid") == 1
    t2 = uuid.uuid4()  # when every item comes from one supplier there is exactly one supplier order
    await _store(catalog, t2)
    assert len((await _paid(orders, t2))["supplier_orders"]) == 1


async def test_a_cancelled_order_cannot_be_paid(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    order = await _order(orders, t)
    await orders.cancel_order(t, uuid.UUID(order["id"]), reason="Changed my mind")
    with pytest.raises(OrderTransitionError):
        await orders.mark_paid(t, uuid.UUID(order["id"]), payment_reference="LATE-1", source="MANUAL")


# --- supplier orders -------------------------------------------------------------------------------------------------------------------------------------


async def test_the_work_queue_shows_what_to_order_and_the_order_rolls_up_only_when_all_are_placed(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t, two_suppliers=True)
    paid = await _paid(orders, t)
    queue = await orders.list_supplier_orders(t, status="TO_PLACE")
    assert len(queue) == 2 and {q["supplier"] for q in queue} == {"Acme Supply", "Beta Foods"}
    acme = next(q for q in queue if q["supplier"] == "Acme Supply")
    assert acme["items"] == [{"supplier_sku": "AS-TEA", "sku_code": "TEA-1", "name": "Green Tea", "quantity": 2}]  # only this supplier's items
    assert acme["ship_to"]["line1"] == "12 Main St" and acme["order_number"] == paid["order_number"]
    beta = next(q for q in queue if q["supplier"] == "Beta Foods")
    for bad in ({}, {"supplier_reference": "   "}, {"supplier_reference": "x" * 161}):
        with pytest.raises(CatalogValidationError):
            await orders.mark_supplier_order(t, uuid.UUID(acme["id"]), "placed", **bad)
    with pytest.raises(CatalogValidationError):
        await orders.mark_supplier_order(t, uuid.UUID(acme["id"]), "failed")  # a note is required
    with pytest.raises(OrderTransitionError):
        await orders.mark_supplier_order(t, uuid.UUID(acme["id"]), "confirmed")  # not placed yet

    one = await orders.mark_supplier_order(t, uuid.UUID(acme["id"]), "placed", supplier_reference="AS-1001", actor_id=uuid.uuid4())
    assert one["status"] == "PAID"  # one of two placed: the order has not moved yet
    both = await orders.mark_supplier_order(t, uuid.UUID(beta["id"]), "placed", supplier_reference="BF-2002", note="phoned in")
    assert both["status"] == "SUPPLIER_ORDER_PLACED"
    assert {so["supplier_reference"] for so in both["supplier_orders"]} == {"AS-1001", "BF-2002"}
    assert (await orders.mark_supplier_order(t, uuid.UUID(acme["id"]), "confirmed"))["supplier_orders"][0]["status"] in ("CONFIRMED", "PLACED")
    assert await orders.list_supplier_orders(t, status="TO_PLACE") == []
    with pytest.raises(OrderTransitionError):
        await orders.mark_supplier_order(t, uuid.UUID(acme["id"]), "placed", supplier_reference="AGAIN")


async def test_a_failed_supplier_order_can_be_retried(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    paid = await _paid(orders, t)
    so_id = uuid.UUID(paid["supplier_orders"][0]["id"])
    failed = await orders.mark_supplier_order(t, so_id, "failed", note="Supplier website was down")
    assert failed["supplier_orders"][0]["status"] == "FAILED" and failed["status"] == "PAID"
    placed = await orders.mark_supplier_order(t, so_id, "placed", supplier_reference="AS-RETRY")
    assert placed["status"] == "SUPPLIER_ORDER_PLACED" and placed["supplier_orders"][0]["supplier_reference"] == "AS-RETRY"


async def test_supplier_orders_cannot_be_worked_on_an_unpaid_or_cancelled_order(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    paid = await _paid(orders, t)
    await orders.cancel_order(t, uuid.UUID(paid["id"]), reason="Customer called")  # paid, nothing placed: allowed, raises a refund flag
    with pytest.raises(OrderTransitionError):
        await orders.mark_supplier_order(t, uuid.UUID(paid["supplier_orders"][0]["id"]), "placed", supplier_reference="TOO-LATE")


# --- tracking and delivery ----------------------------------------------------------------------------------------------------------------------------


async def test_tracking_rolls_the_order_up_to_shipped_then_delivered(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t, two_suppliers=True)
    paid = await _paid(orders, t)
    a, b = (uuid.UUID(so["id"]) for so in paid["supplier_orders"])
    with pytest.raises(OrderTransitionError):
        await orders.add_shipment(t, a, carrier="UPS", tracking_number="1Z999AA10123456784")  # not placed yet
    await orders.mark_supplier_order(t, a, "placed", supplier_reference="R-A")
    await orders.mark_supplier_order(t, b, "placed", supplier_reference="R-B")
    with pytest.raises(CatalogValidationError):
        await orders.add_shipment(t, a, carrier="UPS", tracking_number="bad;number")
    with pytest.raises(CatalogValidationError):
        await orders.add_shipment(t, a, carrier="UPS", tracking_number="1Z999AA10123456784", tracking_url="http://insecure.example")
    first = await orders.add_shipment(t, a, carrier="UPS", tracking_number="1Z999AA10123456784", tracking_url="https://track.example/1Z", actor_id=uuid.uuid4())
    assert first["status"] == "SUPPLIER_ORDER_PLACED"  # only one of two supplier orders has tracking
    second = await orders.add_shipment(t, b, carrier="USPS", tracking_number="9400111899223344556677")
    assert second["status"] == "SHIPPED"
    extra = await orders.add_shipment(t, b, carrier="USPS", tracking_number="9400111899223344556688")  # a second parcel after SHIPPED is fine
    assert extra["status"] == "SHIPPED" and sum(len(so["shipments"]) for so in extra["supplier_orders"]) == 3

    ids = [uuid.UUID(sh["id"]) for so in extra["supplier_orders"] for sh in so["shipments"]]
    for sid in ids[:-1]:
        assert (await orders.mark_shipment(t, sid, "delivered", actor_id=uuid.uuid4()))["status"] == "SHIPPED"  # not every parcel is delivered yet
    done = await orders.mark_shipment(t, ids[-1], "delivered")
    assert done["status"] == "DELIVERED" and done["delivered_at"]
    with pytest.raises(OrderTransitionError):
        await orders.mark_shipment(t, ids[0], "delivered")  # already delivered
    assert {"dropshipping.order.shipped", "dropshipping.order.delivered", "dropshipping.shipment.added"} <= set(await _actions(t))


async def test_a_shipment_exception_needs_a_note_and_can_still_be_delivered_later(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    paid = await _paid(orders, t)
    so = uuid.UUID(paid["supplier_orders"][0]["id"])
    await orders.mark_supplier_order(t, so, "placed", supplier_reference="R-1")
    shipped = await orders.add_shipment(t, so, carrier="UPS", tracking_number="1Z999AA10123456784")
    sid = uuid.UUID(shipped["supplier_orders"][0]["shipments"][0]["id"])
    with pytest.raises(CatalogValidationError):
        await orders.mark_shipment(t, sid, "exception")
    with pytest.raises(CatalogValidationError):
        await orders.mark_shipment(t, sid, "teleported")
    flagged = await orders.mark_shipment(t, sid, "exception", note="Carrier reports the parcel is lost")
    assert flagged["supplier_orders"][0]["shipments"][0]["status"] == ShipmentStatus.EXCEPTION and "lost" in flagged["supplier_orders"][0]["shipments"][0]["note"]
    assert flagged["status"] == "SHIPPED"
    assert (await orders.mark_shipment(t, sid, "delivered"))["status"] == "DELIVERED"  # it turned up


# --- cancel, refund flag, erasure -----------------------------------------------------------------------------------------------------------------------


async def test_cancelling_follows_the_rules_and_never_moves_money(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    unpaid = await _order(orders, t)
    with pytest.raises(CatalogValidationError):
        await orders.cancel_order(t, uuid.UUID(unpaid["id"]), reason="  ")
    cancelled = await orders.cancel_order(t, uuid.UUID(unpaid["id"]), reason="Changed my mind", actor_id=uuid.uuid4())
    assert cancelled["status"] == "CANCELLED" and cancelled["cancelled_at"] and cancelled["status_note"] == "Changed my mind"

    paid = await _paid(orders, t)
    refund = await orders.cancel_order(t, uuid.UUID(paid["id"]), reason="Duplicate order")
    assert refund["status"] == "REFUND_REQUESTED" and "refund needed" in refund["status_note"].lower()
    assert {so["status"] for so in refund["supplier_orders"]} == {"CANCELLED"}  # nothing is left to place
    with pytest.raises(OrderTransitionError):
        await orders.cancel_order(t, uuid.UUID(paid["id"]), reason="again")

    placed = await _paid(orders, t)
    await orders.mark_supplier_order(t, uuid.UUID(placed["supplier_orders"][0]["id"]), "placed", supplier_reference="R-9")
    with pytest.raises(OrderTransitionError):  # a supplier order is already placed: use a refund request instead
        await orders.cancel_order(t, uuid.UUID(placed["id"]), reason="too late")
    flagged = await orders.request_refund(t, uuid.UUID(placed["id"]), reason="Damaged in transit")
    assert flagged["status"] == "REFUND_REQUESTED" and "Damaged" in flagged["status_note"]
    with pytest.raises(OrderTransitionError):
        await orders.request_refund(t, uuid.UUID(unpaid["id"]), reason="x")  # a cancelled order has nothing to refund


async def test_erasure_clears_personal_data_keeps_the_facts_and_kills_the_status_link(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    paid = await _paid(orders, t)
    oid = uuid.UUID(paid["id"])
    async with async_session_maker() as session:
        token_before = (await session.execute(select(Order.public_token).where(Order.id == oid))).scalar_one()
    with pytest.raises(OrderTransitionError):  # still in flight
        await orders.erase_personal_data(t, oid)
    await orders.cancel_order(t, oid, reason="Customer asked us to stop")
    erased = await orders.erase_personal_data(t, oid, actor_id=uuid.uuid4())
    assert erased["personal_data_erased"] is True and erased["contact_email"] is None and erased["contact_phone"] is None and erased["ship_to"] is None
    assert erased["total"] == paid["total"] and len(erased["lines"]) == 2 and erased["order_number"] == paid["order_number"]  # the facts stay
    async with async_session_maker() as session:
        token_after = (await session.execute(select(Order.public_token).where(Order.id == oid))).scalar_one()
    assert token_after != token_before  # the old link no longer finds anything
    again = await orders.erase_personal_data(t, oid)
    assert again["personal_data_erased"] is True and (await _actions(t)).count("dropshipping.order.personal_data_erased") == 1  # idempotent
    queue = await orders.list_supplier_orders(t)
    assert all(q["ship_to"] is None for q in queue)  # the work queue no longer shows the address either


# --- the tool --------------------------------------------------------------------------------------------------------------------------------------------


def _ctx(tenant: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_search_orders_is_auto_read_only_and_returns_no_personal_data(tool_registry, catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    paid = await _paid(orders, t)
    so = uuid.UUID(paid["supplier_orders"][0]["id"])
    await orders.mark_supplier_order(t, so, "placed", supplier_reference="SECRET-SUPPLIER-REF")
    await orders.add_shipment(t, so, carrier="UPS", tracking_number="1Z999AA10123456784", tracking_url="https://track.example/1Z")
    assert DEFAULT_TOOL_POLICIES["dropshipping.search_orders"] == ActionPolicy.AUTO
    assert tool_registry.get("dropshipping.search_orders").supports_idempotency is True
    out = await tool_registry.execute("dropshipping.search_orders", {"q": paid["order_number"][-5:]}, _ctx(t, Role.READ_ONLY))
    (found,) = out.orders
    assert found["order_number"] == paid["order_number"] and found["status"] == "SHIPPED" and found["total"] == paid["total"]
    assert found["supplier_orders"][0]["shipments"][0]["tracking_number"] == "1Z999AA10123456784"
    text = json.dumps(out.model_dump(mode="json"))
    for private in ("pat@example.com", "512-555-0147", "12 Main St", "Austin", "78701", "Pat Shopper", "BANK-"):
        assert private not in text, private
    for key in ("contact_email", "contact_phone", "ship_to", "payment_reference", "customer_id"):
        assert key not in found
    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("dropshipping.search_orders", {}, _ctx(t, Role.TECHNICIAN))
    assert (await tool_registry.execute("dropshipping.search_orders", {"status": "PAID"}, _ctx(uuid.uuid4()))).orders == []  # another business sees nothing


async def test_sku_and_supplier_order_rows_are_tenant_scoped(catalog, orders) -> None:
    t = uuid.uuid4()
    await _store(catalog, t)
    paid = await _paid(orders, t)
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(SupplierOrder).where(SupplierOrder.tenant_id == t))).scalar_one() == 1
        assert (await session.execute(select(func.count()).select_from(Sku).where(Sku.tenant_id == t))).scalar_one() == 2
    assert paid["supplier_orders"][0]["status"] == SupplierOrderStatus.TO_PLACE
    assert Decimal(paid["total"]) == Decimal("24.50")
