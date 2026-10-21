"""Dropshipping store: the e-mails a shopper gets about their order. They go out once per event, only when the event really happened, carry only facts the order holds, and a
failed send never undoes or blocks the order's own state change."""

import uuid
from datetime import datetime, timezone

import pytest

from app.db.session import async_session_maker
from app.models.dropshipping import Order
from app.services import dropshipping_order_emails as emails
from app.services.dropshipping_catalog_service import DropshippingCatalogService
from app.services.dropshipping_order_service import DropshippingOrderService

SHIP = {"name": "Pat Shopper", "line1": "12 Main St", "city": "Austin", "state": "TX", "postal_code": "78701"}
WHO = {"name": "Pat Shopper", "email": "pat@example.com", "phone": "512-555-0147"}


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    box: list[dict] = []

    async def fake(**kw) -> bool:
        box.append(kw)
        return True

    monkeypatch.setattr(emails, "send_system_email", fake)
    return box


@pytest.fixture
def catalog() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


@pytest.fixture
def orders() -> DropshippingOrderService:
    return DropshippingOrderService(async_session_maker)


async def _paid_order(catalog, orders, *, two_suppliers: bool = False) -> tuple[uuid.UUID, dict]:
    t = uuid.uuid4()
    await catalog.create_supplier(t, "Acme Supply")
    await catalog.create_supplier(t, "Beta Foods")
    for name, code, price, supplier in (("Green Tea", "TEA-1", "10.00", "Acme Supply"), ("Brown Rice", "RICE-1", "4.50", "Beta Foods" if two_suppliers else "Acme Supply")):
        product = await catalog.upsert_product(t, {"name": name, "is_non_perishable": True, "skus": [{"sku_code": code, "price": price, "supplier": supplier, "supplier_sku": code, "availability": "AVAILABLE"}]})
        await catalog.publish_product(t, uuid.UUID(product["id"]))
    order, _ = await orders.create_order(
        t, lines=[{"sku": "TEA-1", "quantity": 2}, {"sku": "RICE-1", "quantity": 1}], contact=WHO, ship_to=SHIP, idempotency_key="idem-" + uuid.uuid4().hex, shipping="5.00", actor_id=uuid.uuid4()
    )
    return t, await orders.mark_paid(t, uuid.UUID(order["id"]), payment_reference="STRIPE-" + uuid.uuid4().hex[:8], source="STRIPE")


async def test_payment_sends_one_confirmation_with_the_real_order(catalog, orders, sent) -> None:
    t, paid = await _paid_order(catalog, orders)
    assert len(sent) == 1
    mail = sent[0]
    assert mail["to"] == "pat@example.com" and mail["from_name"] == "Clean Room"
    assert paid["order_number"] in mail["subject"]
    assert "2 x Green Tea: 20.00 USD" in mail["body"] and "Shipping: 5.00 USD" in mail["body"] and "Total: 29.50 USD" in mail["body"]
    assert "/store/orders/" in mail["body"]
    again = await orders.mark_paid(t, uuid.UUID(paid["id"]), payment_reference=paid["payment_reference"], source="STRIPE")  # a replayed webhook
    assert again["status"] == "PAID" and len(sent) == 1


async def test_shipped_email_goes_once_when_the_last_parcel_gets_tracking_and_carries_it(catalog, orders, sent) -> None:
    t, paid = await _paid_order(catalog, orders, two_suppliers=True)
    a, b = (uuid.UUID(so["id"]) for so in paid["supplier_orders"])
    for so in (a, b):
        await orders.mark_supplier_order(t, so, "placed", supplier_reference="R-" + str(so)[:4])
    sent.clear()
    await orders.add_shipment(t, a, carrier="UPS", tracking_number="1Z999AA10123456784", tracking_url="https://track.example/1Z")
    assert sent == []  # one of two supplier orders: not shipped yet
    shipped = await orders.add_shipment(t, b, carrier="USPS", tracking_number="9400111899223344556677")
    assert shipped["status"] == "SHIPPED" and len(sent) == 1
    body = sent[0]["body"]
    assert "UPS 1Z999AA10123456784" in body and "https://track.example/1Z" in body and "USPS 9400111899223344556677" in body
    await orders.add_shipment(t, b, carrier="USPS", tracking_number="9400111899223344556688")  # a second parcel afterwards
    assert len(sent) == 1


async def test_delivered_email_goes_once_when_every_parcel_is_delivered(catalog, orders, sent) -> None:
    t, paid = await _paid_order(catalog, orders)
    so = uuid.UUID(paid["supplier_orders"][0]["id"])
    await orders.mark_supplier_order(t, so, "placed", supplier_reference="R-1")
    shipped = await orders.add_shipment(t, so, carrier="UPS", tracking_number="1Z999AA10123456784")
    sent.clear()
    done = await orders.mark_shipment(t, uuid.UUID(shipped["supplier_orders"][0]["shipments"][0]["id"]), "delivered")
    assert done["status"] == "DELIVERED" and len(sent) == 1 and "was delivered" in sent[0]["subject"]


async def test_a_failing_provider_never_blocks_or_undoes_the_order(catalog, orders, monkeypatch) -> None:
    async def boom(**kw) -> bool:
        raise RuntimeError("provider down")

    monkeypatch.setattr(emails, "send_system_email", boom)
    t, paid = await _paid_order(catalog, orders)
    assert paid["status"] == "PAID"
    assert (await orders.get_order(t, uuid.UUID(paid["id"])))["status"] == "PAID"


async def test_no_address_or_an_erased_order_sends_nothing(sent) -> None:
    erased = Order(contact_email=None, personal_data_erased_at=datetime.now(timezone.utc), public_token="x", order_number="N-1")
    assert await emails.notify_order(emails.PAID, erased, {}) is False
    still = Order(contact_email="pat@example.com", personal_data_erased_at=datetime.now(timezone.utc), public_token="x", order_number="N-1")
    assert await emails.notify_order(emails.PAID, still, {}) is False
    assert sent == []


def test_an_unknown_event_is_refused_and_the_text_makes_no_promises() -> None:
    order = {"order_number": "DS-1", "currency": "USD", "shipping": "0", "total": "5", "lines": [], "supplier_orders": [], "ship_to": {}}
    with pytest.raises(ValueError):
        emails.build_order_email("promo", order, store_name="Clean Room", status_url="https://x.example/o")
    _subject, body = emails.build_order_email(emails.DELIVERED, order, store_name="Clean Room", status_url="https://x.example/o")
    assert body.startswith("Hello,") and "https://x.example/o" in body
