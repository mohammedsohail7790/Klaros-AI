"""Dropshipping: the store's morning e-mail to its owners: right numbers, nothing about customers, once a day, never empty, only this store's data."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.db.session import async_session_maker
from app.models.dropshipping import InventorySnapshot, Order, Sku
from app.models.rbac import Role
from app.services import dropshipping_digest as digest
from app.services.dropshipping_catalog_service import DropshippingCatalogService
from app.services.dropshipping_order_service import DropshippingOrderService
from tests.test_dropshipping_api import BASE, _h, _tenant_id, _user_token
from tests.test_business_journey_api import _register

SHIP = {"name": "Pat Shopper", "line1": "12 Main St", "city": "Austin", "state": "TX", "postal_code": "78701"}
WHO = {"name": "Pat Shopper", "email": "pat@example.com", "phone": "512-555-0147"}


class _Channel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_email(self, tenant_id, **kw):
        self.sent.append(kw)
        return True


@pytest.fixture
def channel(monkeypatch) -> _Channel:
    import app.communications.email_config as cfg
    import app.communications.factory as factory

    ch = _Channel()
    monkeypatch.setattr(cfg, "email_configured", lambda: True)
    monkeypatch.setattr(factory, "get_internal_channel", lambda _sf: ch)
    return ch


@pytest.fixture
def catalog() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


@pytest.fixture
def orders() -> DropshippingOrderService:
    return DropshippingOrderService(async_session_maker)


async def _shop(catalog, t) -> None:
    await catalog.create_supplier(t, "Acme Supply")
    for name, code, price in (("Green Tea", "TEA-1", "10.00"), ("Brown Rice", "RICE-1", "4.50"), ("Wild Honey", "HON-1", "12.00")):
        product = await catalog.upsert_product(t, {"name": name, "is_non_perishable": True, "skus": [{"sku_code": code, "price": price, "supplier": "Acme Supply", "supplier_sku": code, "availability": "AVAILABLE"}]})
        await catalog.publish_product(t, uuid.UUID(product["id"]))


async def _order(orders, t, lines, *, pay: bool = True) -> dict:
    o, _ = await orders.create_order(t, lines=lines, contact=WHO, ship_to=SHIP, idempotency_key="idem-" + uuid.uuid4().hex, shipping="5.00", actor_id=uuid.uuid4())
    return await orders.mark_paid(t, uuid.UUID(o["id"]), payment_reference="STRIPE-" + uuid.uuid4().hex[:8], source="STRIPE") if pay else o


async def _tenant_with_owner(client, org: str, email: str) -> tuple[str, uuid.UUID]:
    owner = await _register(client, org, email)
    return owner, await _tenant_id(client, owner)


async def test_a_busy_day_is_summed_up_with_what_waits_and_what_is_out_or_low(client, catalog, orders, channel) -> None:
    owner, t = await _tenant_with_owner(client, "Digest Shop", "digest-owner@example.com")
    await _shop(catalog, t)
    await _order(orders, t, [{"sku": "TEA-1", "quantity": 2}])              # 20.00 + 5.00 shipping = 25.00
    second = await _order(orders, t, [{"sku": "RICE-1", "quantity": 1}])    # 4.50 + 5.00 = 9.50
    await _order(orders, t, [{"sku": "HON-1", "quantity": 1}], pay=False)   # placed, not paid
    await orders.request_refund(t, uuid.UUID(second["id"]), reason="damaged on arrival")
    async with async_session_maker() as session:
        await session.execute(update(Sku).where(Sku.tenant_id == t, Sku.sku_code == "HON-1").values(availability="OUT_OF_STOCK"))
        tea = (await session.execute(Sku.__table__.select().where(Sku.tenant_id == t, Sku.sku_code == "TEA-1"))).first()
        session.add(InventorySnapshot(tenant_id=t, sku_id=tea.id, quantity=3, source="FEED", observed_at=datetime.now(timezone.utc)))
        await session.commit()

    data = await digest.gather(async_session_maker, t)
    assert data["orders_24h"] == 3 and data["paid_24h"] == 2 and str(data["revenue_24h"]) == "34.50" and str(data["revenue_7d"]) == "34.50"
    assert data["waiting_to_order"] == 2 and data["refunds"] == [second["order_number"]]
    assert data["sold_out"] == ["Wild Honey (HON-1)"] and data["low"] == ["Green Tea (TEA-1): 3 left"]
    subject, body = digest.compose(data, "https://app.example.com")
    assert "2 paid orders, $34.50 yesterday" in subject and "waiting on you" in subject
    for fragment in ("Orders placed: 3", "Revenue: $34.50", "2 supplier orders to place", second["order_number"], "Sold out (1)", "Wild Honey (HON-1)", "Running low (1)", "https://app.example.com/dropshipping/fulfilment"):
        assert fragment in body, fragment
    for private in ("pat@example.com", "Pat Shopper", "12 Main St", "512-555-0147"):
        assert private not in body and private not in subject


async def test_it_goes_to_the_owner_once_a_day_and_never_on_a_quiet_one(client, catalog, orders, channel) -> None:
    owner, t = await _tenant_with_owner(client, "Daily Shop", "daily-owner@example.com")
    await _shop(catalog, t)
    assert await digest.send_store_digest(async_session_maker, t) == {"sent": 0, "reason": "quiet_day"} and channel.sent == []
    await _order(orders, t, [{"sku": "TEA-1", "quantity": 1}])
    first = await digest.send_store_digest(async_session_maker, t)
    assert first == {"sent": 1, "reason": None}
    assert channel.sent[0]["to"] == "daily-owner@example.com" and "1 paid order," in channel.sent[0]["subject"]
    assert await digest.send_store_digest(async_session_maker, t) == {"sent": 0, "reason": "already_sent_today"} and len(channel.sent) == 1


async def test_a_paid_order_nobody_shipped_for_two_days_is_flagged(client, catalog, orders, channel) -> None:
    owner, t = await _tenant_with_owner(client, "Stuck Shop", "stuck-owner@example.com")
    await _shop(catalog, t)
    paid = await _order(orders, t, [{"sku": "TEA-1", "quantity": 1}])
    async with async_session_maker() as session:
        await session.execute(update(Order).where(Order.id == uuid.UUID(paid["id"])).values(paid_at=datetime.now(timezone.utc) - timedelta(days=3)))
        await session.commit()
    data = await digest.gather(async_session_maker, t)
    assert data["stuck"] == [paid["order_number"]] and data["paid_24h"] == 0
    assert paid["order_number"] in digest.compose(data, "https://x")[1] and not digest.is_quiet(data)


async def test_nothing_is_sent_without_an_email_provider_and_another_stores_data_never_appears(client, catalog, orders, monkeypatch) -> None:
    import app.communications.email_config as cfg

    owner_a, a = await _tenant_with_owner(client, "Shop A", "shop-a@example.com")
    owner_b, b = await _tenant_with_owner(client, "Shop B", "shop-b@example.com")
    await _shop(catalog, a)
    await _order(orders, a, [{"sku": "TEA-1", "quantity": 1}])
    monkeypatch.setattr(cfg, "email_configured", lambda: False)
    assert await digest.send_store_digest(async_session_maker, a) == {"sent": 0, "reason": "email_not_configured"}
    other = await digest.gather(async_session_maker, b)
    assert other["orders_24h"] == 0 and other["sold_out"] == [] and digest.is_quiet(other)


async def test_the_routes_preview_for_staff_and_send_only_for_managers(client, catalog, orders, channel) -> None:
    owner, t = await _tenant_with_owner(client, "Route Shop", "route-owner@example.com")
    await _shop(catalog, t)
    await _order(orders, t, [{"sku": "TEA-1", "quantity": 1}])
    staff = await _user_token(client, owner, "route-staff@example.com", Role.STAFF)
    assert (await client.get(f"{BASE}/digest/preview")).status_code == 401
    preview = await client.get(f"{BASE}/digest/preview", headers=_h(staff))
    assert preview.status_code == 200 and "1 paid order" in preview.json()["subject"] and preview.json()["quiet_day"] is False and channel.sent == []  # a preview sends nothing
    assert (await client.post(f"{BASE}/digest/send", headers=_h(staff))).status_code == 403
    sent = await client.post(f"{BASE}/digest/send", headers=_h(owner))
    assert sent.status_code == 200 and sent.json() == {"sent": 1, "reason": None}
    assert (await client.post(f"{BASE}/digest/send", headers=_h(owner))).json()["reason"] == "already_sent_today"
