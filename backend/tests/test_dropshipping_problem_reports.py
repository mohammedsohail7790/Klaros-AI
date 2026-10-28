"""A shopper reports that something is wrong with what arrived; staff approve (the order is flagged for a refund, Klaros moves no money) or reject with a reason."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.dropshipping import Order, ProblemReport
from app.models.rbac import Role
from app.services import dropshipping_digest as digest
from app.services import dropshipping_order_emails as emails
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError, DropshippingCatalogService
from app.services.dropshipping_order_rules import OrderTransitionError
from app.services.dropshipping_order_service import DropshippingOrderService
from app.services.dropshipping_problem_service import DropshippingProblemService, can_report
from tests.test_dropshipping_api import _h, _user_token
from tests.test_dropshipping_orders_api import BASE, _create, _owner_with_store

SHIP = {"name": "Pat Shopper", "line1": "12 Main St", "city": "Austin", "state": "TX", "postal_code": "78701"}
WHO = {"name": "Pat Shopper", "email": "pat@example.com", "phone": "512-555-0147"}
TEXT = "The jar of honey arrived broken and leaked over the rice."


@pytest.fixture(autouse=True)
def _no_mail(monkeypatch) -> None:
    async def fake(**_kw) -> bool:
        return True

    monkeypatch.setattr(emails, "send_system_email", fake)


@pytest.fixture
def problems() -> DropshippingProblemService:
    return DropshippingProblemService(async_session_maker)


@pytest.fixture
def orders() -> DropshippingOrderService:
    return DropshippingOrderService(async_session_maker)


async def _paid(orders) -> tuple[uuid.UUID, dict]:
    t = uuid.uuid4()
    catalog = DropshippingCatalogService(async_session_maker)
    await catalog.create_supplier(t, "Acme Supply")
    product = await catalog.upsert_product(t, {"name": "Green Tea", "is_non_perishable": True, "skus": [{"sku_code": "TEA-1", "price": "10.00", "supplier": "Acme Supply", "supplier_sku": "TEA-1", "availability": "AVAILABLE"}]})
    await catalog.publish_product(t, uuid.UUID(product["id"]))
    order, _ = await orders.create_order(t, lines=[{"sku": "TEA-1", "quantity": 2}], contact=WHO, ship_to=SHIP, idempotency_key="idem-" + uuid.uuid4().hex, shipping="5.00", actor_id=uuid.uuid4())
    return t, await orders.mark_paid(t, uuid.UUID(order["id"]), payment_reference="STRIPE-" + uuid.uuid4().hex[:8], source="STRIPE")


async def _shipped(orders) -> tuple[uuid.UUID, dict]:
    t, paid = await _paid(orders)
    so = uuid.UUID(paid["supplier_orders"][0]["id"])
    await orders.mark_supplier_order(t, so, "placed", supplier_reference="R-1")
    return t, await orders.add_shipment(t, so, carrier="UPS", tracking_number="1Z999AA10123456784")


async def _token(order_number: str) -> str:
    async with async_session_maker() as session:
        return (await session.execute(select(Order.public_token).where(Order.order_number == order_number))).scalar_one()


async def test_only_a_shipped_order_can_be_reported_and_only_once(orders, problems) -> None:
    t, paid = await _paid(orders)
    token = await _token(paid["order_number"])
    with pytest.raises(CatalogValidationError):
        await problems.report(t, token, TEXT)                                     # not shipped yet: nothing can have arrived
    t2, shipped = await _shipped(orders)
    token2 = await _token(shipped["order_number"])
    await problems.report(t2, token2, TEXT)
    assert [r["status"] for r in await problems.list(t2)] == ["PENDING"] and await problems.pending_count(t2) == 1
    with pytest.raises(CatalogConflictError):
        await problems.report(t2, token2, TEXT)                                   # one open report per order
    with pytest.raises(CatalogNotFoundError):
        await problems.report(t2, "no-such-token", TEXT)
    with pytest.raises(CatalogNotFoundError):
        await problems.report(uuid.uuid4(), token2, TEXT)                         # another store's token means nothing here


@pytest.mark.parametrize("bad", ["", "   ", "short", "x" * 1001, None])
async def test_the_description_must_be_a_real_sentence_of_sensible_length(orders, problems, bad) -> None:
    t, shipped = await _shipped(orders)
    with pytest.raises(CatalogValidationError):
        await problems.report(t, await _token(shipped["order_number"]), bad)
    assert await problems.list(t) == []


async def test_the_window_is_counted_from_payment_and_can_be_set(orders, problems, monkeypatch) -> None:
    t, shipped = await _shipped(orders)
    token = await _token(shipped["order_number"])
    late = datetime.now(timezone.utc) + timedelta(days=31)
    with pytest.raises(CatalogValidationError):
        await problems.report(t, token, TEXT, now=late)
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_PROBLEM_WINDOW_DAYS", 60)
    await problems.report(t, token, TEXT, now=late)
    async with async_session_maker() as session:
        order = (await session.execute(select(Order).where(Order.order_number == shipped["order_number"]))).scalar_one()
    assert can_report(order, False, now=datetime.now(timezone.utc)) is True and can_report(order, True) is False


async def test_approving_flags_the_order_for_a_refund_and_closes_the_report(orders, problems) -> None:
    t, shipped = await _shipped(orders)
    await problems.report(t, await _token(shipped["order_number"]), TEXT)
    [report] = await problems.list(t, status="PENDING")
    out = await problems.decide(t, uuid.UUID(report["id"]), approve=True, actor_id=uuid.uuid4())
    assert out["status"] == "APPROVED" and await problems.pending_count(t) == 0
    async with async_session_maker() as session:
        order = (await session.execute(select(Order).where(Order.order_number == shipped["order_number"]))).scalar_one()
    assert order.status == "REFUND_REQUESTED" and "honey" in (order.status_note or "")
    with pytest.raises(CatalogConflictError):
        await problems.decide(t, uuid.UUID(report["id"]), approve=False, note="too late")      # decided once


async def test_rejecting_needs_a_reason_and_leaves_the_order_alone(orders, problems) -> None:
    t, shipped = await _shipped(orders)
    await problems.report(t, await _token(shipped["order_number"]), TEXT)
    [report] = await problems.list(t)
    with pytest.raises(CatalogValidationError):
        await problems.decide(t, uuid.UUID(report["id"]), approve=False, note="  ")
    out = await problems.decide(t, uuid.UUID(report["id"]), approve=False, note="The photos show the jar was whole.")
    assert out["status"] == "REJECTED"
    async with async_session_maker() as session:
        order = (await session.execute(select(Order).where(Order.order_number == shipped["order_number"]))).scalar_one()
        row = (await session.execute(select(ProblemReport))).scalars().first()
    assert order.status == "SHIPPED" and row.decision_note == "The photos show the jar was whole."
    await problems.report(t, await _token(shipped["order_number"]), TEXT + " Again.")        # after a rejection the shopper may write again


async def test_an_order_that_can_no_longer_be_refunded_keeps_the_report_pending(orders, problems) -> None:
    t, shipped = await _shipped(orders)
    await problems.report(t, await _token(shipped["order_number"]), TEXT)
    [report] = await problems.list(t)
    await orders.request_refund(t, uuid.UUID(shipped["id"]), reason="already flagged by staff")   # REFUND_REQUESTED has no way out
    with pytest.raises(OrderTransitionError):
        await problems.decide(t, uuid.UUID(report["id"]), approve=True)
    assert (await problems.list(t))[0]["status"] == "PENDING"


async def test_the_daily_summary_counts_waiting_reports_without_naming_the_shopper(orders, problems) -> None:
    t, shipped = await _shipped(orders)
    await problems.report(t, await _token(shipped["order_number"]), TEXT)
    data = await digest.gather(async_session_maker, t)
    assert data["problems"] == 1 and digest.needs_attention(data)
    body = digest.compose(data, "https://app.example.com")[1]
    assert "1 shopper report that something is wrong with an order, to review" in body
    for private in ("honey", "Pat Shopper", "pat@example.com", "12 Main St"):
        assert private not in body


async def test_the_routes_let_staff_review_and_the_shopper_report_through_the_store(client, monkeypatch) -> None:
    owner = await _owner_with_store(client, "Problem Co", "problem-co@example.com")
    staff = await _user_token(client, owner, "staff@problem-co.example.com", Role.STAFF)
    reader = await _user_token(client, owner, "reader@problem-co.example.com", Role.READ_ONLY)
    order = await _create(client, staff)
    assert (await client.post(f"{BASE}/orders/{order['id']}/mark-paid", json={"payment_reference": "BANK-9"}, headers=_h(owner))).status_code == 200
    queue = (await client.get(f"{BASE}/supplier-orders?status=TO_PLACE", headers=_h(staff))).json()["supplier_orders"]
    for so in queue:
        assert (await client.post(f"{BASE}/supplier-orders/{so['id']}/placed", json={"supplier_reference": "R"}, headers=_h(staff))).status_code == 200
        assert (await client.post(f"{BASE}/supplier-orders/{so['id']}/shipments", json={"carrier": "UPS", "tracking_number": "1Z999AA10123456784"}, headers=_h(staff))).status_code == 200
    from app.db.session import async_session_maker as maker
    from app.models.dropshipping import Order as O

    async with maker() as session:
        row = (await session.execute(select(O).where(O.order_number == order["order_number"]))).scalar_one()
        tenant, token = row.tenant_id, row.public_token
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_STORE_TENANT_ID", str(tenant))
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_PUBLIC_STORE_CHECKOUT_PER_MINUTE", 100)

    shop = f"/api/v1/public/store/orders/{token}"
    page = await client.get(shop)
    assert page.status_code == 200 and page.json()["order"]["can_report_problem"] is True and page.json()["order"]["problem"] is None
    assert (await client.post(f"{shop}/problem", json={"description": "short"})).status_code == 422
    assert (await client.post(f"{shop}/problem", json={"description": TEXT, "order_id": "x"})).status_code == 422          # unknown fields are refused
    ok = await client.post(f"{shop}/problem", json={"description": TEXT})
    assert ok.status_code == 201 and ok.json() == {"received": True}
    assert (await client.post(f"{shop}/problem", json={"description": TEXT})).status_code == 409
    after = (await client.get(shop)).json()["order"]
    assert after["can_report_problem"] is False and after["problem"] == {"status": "PENDING"} and "honey" not in str(after)

    listed = (await client.get(f"{BASE}/problem-reports?status=PENDING", headers=_h(reader))).json()["problem_reports"]
    assert [r["order_number"] for r in listed] == [order["order_number"]] and listed[0]["description"] == TEXT                   # looking is allowed
    rid = listed[0]["id"]
    assert (await client.post(f"{BASE}/problem-reports/{rid}/approve", headers=_h(reader))).status_code == 403                  # deciding is not
    assert (await client.post(f"{BASE}/problem-reports/{rid}/maybe", headers=_h(staff))).status_code == 404
    assert (await client.post(f"{BASE}/problem-reports/{rid}/reject", json={}, headers=_h(staff))).status_code == 422
    done = await client.post(f"{BASE}/problem-reports/{rid}/approve", headers=_h(staff))
    assert done.status_code == 200 and done.json()["problem_report"]["status"] == "APPROVED"
    assert (await client.get(f"{BASE}/orders/{order['id']}", headers=_h(staff))).json()["order"]["status"] == "REFUND_REQUESTED"
    assert (await client.get(f"{BASE}/problem-reports", headers={})).status_code in (401, 403)
