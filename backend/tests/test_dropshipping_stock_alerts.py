"""'Tell me when it is back': stored only for a sold-out item on sale, one plain message when it is available again, and the address removed as soon as the shopper has been told."""

import uuid

import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.dropshipping import Sku, StockAlert
from app.services import dropshipping_stock_alerts as alerts
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError
from tests.test_dropshipping_api import _h
from tests.test_dropshipping_orders_api import BASE
from tests.test_dropshipping_store_api import _live_store, stripe  # noqa: F401  (stripe is a fixture)

ALERTS = "/api/v1/public/store/stock-alerts"


class Mailbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accept = True

    async def __call__(self, *, to, subject, body, from_name=None, **_):
        if self.accept:
            self.sent.append({"to": to, "subject": subject, "body": body, "from_name": from_name})
        return self.accept


@pytest.fixture
def mailbox(monkeypatch) -> Mailbox:
    box = Mailbox()
    monkeypatch.setattr(alerts, "send_system_email", box)
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_PUBLIC_STORE_ENQUIRY_PER_MINUTE", 100)
    return box


async def _set(tenant, code: str, availability: str) -> None:
    async with async_session_maker() as session:
        await session.execute(update(Sku).where(Sku.tenant_id == tenant, Sku.sku_code == code).values(availability=availability))
        await session.commit()


async def _open_rows(tenant) -> list[StockAlert]:
    async with async_session_maker() as session:
        return list((await session.execute(select(StockAlert).where(StockAlert.tenant_id == tenant, StockAlert.notified_at.is_(None)))).scalars())


async def test_a_sold_out_item_can_be_waited_for_once_per_address(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant = await _live_store(client, monkeypatch, "Alert Co", "alert-co@example.com")
    await _set(tenant, "TEA-1", "OUT_OF_STOCK")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "  Sam@Example.com ")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "sam@example.com")             # the same person again: no second row
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "kim@example.com")
    assert sorted(a.email for a in await _open_rows(tenant)) == ["kim@example.com", "sam@example.com"]

    with pytest.raises(CatalogConflictError):
        await alerts.subscribe(async_session_maker, tenant, "RICE-1", "sam@example.com")        # in stock: there is nothing to wait for
    with pytest.raises(CatalogNotFoundError):
        await alerts.subscribe(async_session_maker, tenant, "NOPE-9", "sam@example.com")
    for bad in ("", "no-at-sign", "a@b", "x" * 300 + "@example.com"):
        with pytest.raises(CatalogValidationError):
            await alerts.subscribe(async_session_maker, tenant, "TEA-1", bad)
    assert len(await _open_rows(tenant)) == 2


async def test_each_item_holds_a_limited_number_of_open_requests(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant = await _live_store(client, monkeypatch, "Cap Co", "cap-co@example.com")
    await _set(tenant, "TEA-1", "OUT_OF_STOCK")
    monkeypatch.setattr(alerts, "MAX_OPEN_PER_SKU", 2)
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "a@example.com")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "b@example.com")
    with pytest.raises(CatalogConflictError):
        await alerts.subscribe(async_session_maker, tenant, "TEA-1", "c@example.com")


async def test_everyone_waiting_is_told_once_when_it_is_back_and_the_address_is_removed(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant = await _live_store(client, monkeypatch, "Back Co", "back-co@example.com")
    await _set(tenant, "TEA-1", "OUT_OF_STOCK")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "sam@example.com")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "kim@example.com")
    assert await alerts.send_due(async_session_maker, tenant) == 0 and mailbox.sent == []      # still sold out

    await _set(tenant, "TEA-1", "AVAILABLE")
    assert await alerts.send_due(async_session_maker, tenant) == 2
    assert {m["to"] for m in mailbox.sent} == {"sam@example.com", "kim@example.com"}
    mail = mailbox.sent[0]
    assert "Green Tea is back in stock" in mail["subject"] and "/store/products/green-tea" in mail["body"] and mail["from_name"] == get_settings().DROPSHIPPING_STORE_NAME
    assert "not a reservation" in mail["body"] and "will not e-mail you again" in mail["body"] and "discount" not in mail["body"].lower()
    async with async_session_maker() as session:
        rows = list((await session.execute(select(StockAlert).where(StockAlert.tenant_id == tenant))).scalars())
    assert len(rows) == 2 and all(r.email is None and r.notified_at is not None for r in rows)    # told, and no address kept
    assert await alerts.send_due(async_session_maker, tenant) == 0                              # never twice

    await _set(tenant, "TEA-1", "OUT_OF_STOCK")                                                 # sold out again: the same person may ask again
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "sam@example.com")
    assert len(await _open_rows(tenant)) == 1


async def test_a_refused_message_is_retried_and_a_closed_store_sends_nothing(client, monkeypatch, stripe, mailbox) -> None:
    owner, tenant = await _live_store(client, monkeypatch, "Retry Alert Co", "retry-alert@example.com")
    await _set(tenant, "TEA-1", "OUT_OF_STOCK")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "sam@example.com")
    await _set(tenant, "TEA-1", "AVAILABLE")

    mailbox.accept = False
    assert await alerts.send_due(async_session_maker, tenant) == 0 and len(await _open_rows(tenant)) == 1      # the provider said no: still waiting
    assert (await client.put(f"{BASE}/store-settings", json={"open": False}, headers=_h(owner))).status_code == 200
    mailbox.accept = True
    assert await alerts.send_due(async_session_maker, tenant) == 0 and mailbox.sent == []                      # closed: the link would not work
    assert (await client.put(f"{BASE}/store-settings", json={"open": True}, headers=_h(owner))).status_code == 200
    assert await alerts.send_due(async_session_maker, tenant) == 1 and mailbox.sent[0]["to"] == "sam@example.com"


async def test_an_item_that_is_no_longer_on_sale_is_never_announced(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant = await _live_store(client, monkeypatch, "Gone Co", "gone-co@example.com")
    await _set(tenant, "TEA-1", "OUT_OF_STOCK")
    await alerts.subscribe(async_session_maker, tenant, "TEA-1", "sam@example.com")
    async with async_session_maker() as session:
        await session.execute(update(Sku).where(Sku.tenant_id == tenant, Sku.sku_code == "TEA-1").values(availability="AVAILABLE", status="INACTIVE"))
        await session.commit()
    assert await alerts.send_due(async_session_maker, tenant) == 0 and mailbox.sent == []


async def test_the_public_route_stores_a_request_ignores_bots_and_refuses_the_rest(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant = await _live_store(client, monkeypatch, "Route Alert Co", "route-alert@example.com")
    await _set(tenant, "TEA-1", "OUT_OF_STOCK")
    ok = await client.post(ALERTS, json={"sku": "TEA-1", "email": "sam@example.com"})
    assert ok.status_code == 201 and ok.json() == {"received": True}
    assert (await client.post(ALERTS, json={"sku": "TEA-1", "email": "sam@example.com"})).json() == {"received": True}       # same answer for a repeat
    bot = await client.post(ALERTS, json={"sku": "TEA-1", "email": "bot@example.com", "website": "http://spam.example"})
    assert bot.status_code == 201 and [a.email for a in await _open_rows(tenant)] == ["sam@example.com"]
    assert (await client.post(ALERTS, json={"sku": "RICE-1", "email": "sam@example.com"})).status_code == 409
    assert (await client.post(ALERTS, json={"sku": "NOPE-9", "email": "sam@example.com"})).status_code == 404
    assert (await client.post(ALERTS, json={"sku": "TEA-1", "email": "nope"})).status_code == 422
    assert (await client.post(ALERTS, json={"sku": "TEA-1", "email": "a@example.com", "tenant_id": str(uuid.uuid4())})).status_code == 422
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_STORE_TENANT_ID", None)
    assert (await client.post(ALERTS, json={"sku": "TEA-1", "email": "a@example.com"})).status_code == 404
    async with async_session_maker() as session:
        assert (await session.execute(select(func.count()).select_from(StockAlert))).scalar_one() == 1
