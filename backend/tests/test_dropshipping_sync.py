"""Dropshipping: the daily stock and price sync from a supplier's CSV feed. The rules (pure), the service against the database, the staff routes and the safeguards that keep a
broken feed from damaging the shop."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.dropshipping import InventorySnapshot, Sku
from app.models.rbac import Role
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogNotFoundError, DropshippingCatalogService
from app.services import dropshipping_sync_service as sync_mod
from app.services.dropshipping_sync_rules import FeedError, SkuView, parse_feed, plan_sync, price_for
from app.services.dropshipping_sync_service import DropshippingSyncService, FeedFetchError, _check_public_https
from tests.test_dropshipping_api import BASE, _h, _owner_with_catalog, _user_token

AVAIL, OOS = "AVAILABLE", "OUT_OF_STOCK"


def _sku(code: str, *, price: str = "10.00", cost: str | None = "6.00", availability: str = AVAIL) -> SkuView:
    return SkuView(id=f"id-{code}", sku_code=code, supplier_sku=code, price=Decimal(price), supplier_cost=Decimal(cost) if cost else None, availability=availability)


def _plan(skus, csv, *, markup="50", buffer=0, move="30", force=False):
    return plan_sync(skus, parse_feed(csv), markup_percent=Decimal(markup) if markup is not None else None, stock_buffer=buffer, max_price_change_percent=Decimal(move), force=force)


# --- the feed ----------------------------------------------------------------------------------------------------------------------------------------


def test_the_feed_is_read_with_loose_column_names_money_signs_and_other_delimiters() -> None:
    feed = parse_feed("Item Number;Wholesale Price;Qty\nA-1;$1,234.50;12\nb-2;3;0\n")
    assert feed.rows["A-1"].cost == Decimal("1234.50") and feed.rows["A-1"].quantity == 12
    assert feed.rows["B-2"].quantity == 0 and feed.problems == []
    words = parse_feed("sku,price,status\nA,5,In Stock\nB,5,Discontinued\n")
    assert words.rows["A"].availability == AVAIL and words.rows["B"].availability == OOS


def test_a_bad_row_is_reported_and_skipped_and_a_useless_feed_is_refused() -> None:
    feed = parse_feed("sku,cost,qty\nA,abc,5\nB,2,x\nC,2,3\nC,9,9\n")
    assert list(feed.rows) == ["C"] and feed.rows["C"].cost == Decimal("2.00")
    assert len(feed.problems) == 3 and any("more than once" in p for p in feed.problems)
    for bad in ("", "   ", "name,colour\nx,y\n", "sku\nA\n", "sku,qty\n,\n"):
        with pytest.raises(FeedError):
            parse_feed(bad)


# --- what the sync decides -----------------------------------------------------------------------------------------------------------------------


def test_quantity_decides_availability_with_a_buffer_and_nothing_else_is_touched() -> None:
    skus = [_sku("A", availability=OOS), _sku("B"), _sku("C"), _sku("D")]
    plan = _plan(skus, "sku,qty\nA,5\nB,0\nC,3\n", markup=None, buffer=3)
    by = {c.sku_code: c for c in plan.changes}
    assert by["A"].availability == AVAIL                      # 5 is above the buffer of 3
    assert by["B"].availability == OOS                        # 0 is at or below it
    assert by["C"].availability == OOS                        # 3 is at the buffer: counts as sold out
    assert "D" not in by and plan.not_in_feed == 1            # absent from the feed: left exactly as it is
    assert all(c.price is None for c in plan.changes)         # no markup: prices are never touched


def test_the_markup_sets_the_price_from_the_cost_and_records_the_cost() -> None:
    assert price_for(Decimal("6.00"), Decimal("50")) == Decimal("9.00") and price_for(Decimal("3.33"), Decimal("25")) == Decimal("4.16")
    plan = _plan([_sku("A", price="9.00", cost="6.00"), _sku("B", price="9.00", cost="6.00")], "sku,cost\nA,6.00\nB,7.00\n", markup="50")
    by = {c.sku_code: c for c in plan.changes}
    assert "A" not in by and plan.unchanged == 1              # same cost, same price: nothing to do
    assert by["B"].price == Decimal("10.50") and by["B"].supplier_cost == Decimal("7.00")


def test_a_big_price_move_is_held_unless_forced() -> None:
    skus = [_sku("A", price="9.00", cost="6.00")]
    held = _plan(skus, "sku,cost\nA,12.00\n", markup="50", move="30")  # 9.00 -> 18.00 is +100%
    assert held.prices_changed == 0 and held.held_prices[0]["to"] == "18.00" and held.changes[0].price is None and held.changes[0].supplier_cost == Decimal("12.00")
    forced = _plan(skus, "sku,cost\nA,12.00\n", markup="50", move="30", force=True)
    assert forced.changes[0].price == Decimal("18.00") and forced.held_prices == []


def test_a_feed_that_would_sell_out_most_of_the_shop_is_held() -> None:
    skus = [_sku(f"S{i}") for i in range(12)]
    zeroed = "sku,qty\n" + "".join(f"S{i},0\n" for i in range(12))
    plan = _plan(skus, zeroed, markup=None)
    assert plan.availability_held and plan.to_out_of_stock == 0 and not any(c.availability for c in plan.changes)
    assert "looks like a broken feed" in " ".join(plan.problems)
    forced = _plan(skus, zeroed, markup=None, force=True)
    assert not forced.availability_held and forced.to_out_of_stock == 12
    few = _plan(skus[:3], "sku,qty\nS0,0\nS1,0\nS2,0\n", markup=None)  # under ten items: a real sell-out is believed
    assert few.to_out_of_stock == 3


def test_a_feed_matching_nothing_changes_nothing() -> None:
    plan = _plan([_sku("A")], "sku,qty\nZ,5\n", markup="50")
    assert plan.changes == [] and plan.matched == 0 and plan.unknown_in_feed == ["Z"] and "None of the feed's codes match" in plan.problems[0]


# --- the service ---------------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def catalog() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


async def _shop(catalog, tenant, *, supplier="Acme Supply") -> uuid.UUID:
    s, _ = await catalog.create_supplier(tenant, supplier)
    for name, code, price, cost in (("Green Tea", "TEA-1", "9.00", "6.00"), ("Brown Rice", "RICE-1", "4.50", "3.00")):
        await catalog.upsert_product(tenant, {"name": name, "is_non_perishable": True, "skus": [{"sku_code": code, "price": price, "supplier": supplier, "supplier_sku": "SUP-" + code, "supplier_cost": cost, "availability": "AVAILABLE"}]})
    return s.id


async def _sku_row(tenant, code):
    async with async_session_maker() as session:
        return (await session.execute(select(Sku).where(Sku.tenant_id == tenant, Sku.sku_code == code))).scalar_one()


SETTINGS = {"feed_url": "https://feeds.example.com/acme.csv", "markup_percent": "50", "stock_buffer": 0, "max_price_change_percent": 30, "sync_enabled": True}
FEED = "supplier_sku,cost,quantity\nSUP-TEA-1,7.00,20\nSUP-RICE-1,3.00,0\nSUP-UNKNOWN,1.00,5\n"


async def test_settings_are_validated_and_stored(catalog) -> None:
    t = uuid.uuid4()
    sid = await _shop(catalog, t)
    svc = DropshippingSyncService(async_session_maker)
    for bad in ({"feed_url": "http://insecure.example/x.csv"}, {"markup_percent": "900"}, {"stock_buffer": -1}, {"max_price_change_percent": 0}, {"sync_enabled": True}):
        with pytest.raises(CatalogValidationError):
            await svc.save_settings(t, sid, bad)
    saved = await svc.save_settings(t, sid, SETTINGS)
    assert saved["sync"]["sync_enabled"] is True and saved["sync"]["markup_percent"] == "50.00"
    with pytest.raises(CatalogNotFoundError):
        await svc.save_settings(uuid.uuid4(), sid, SETTINGS)  # another business cannot reach this supplier


async def test_a_preview_changes_nothing_and_applying_updates_stock_price_cost_and_leaves_a_trail(catalog) -> None:
    t = uuid.uuid4()
    sid = await _shop(catalog, t)
    svc = DropshippingSyncService(async_session_maker)
    await svc.save_settings(t, sid, SETTINGS)
    preview = await svc.run(t, sid, csv_text=FEED, dry_run=True)
    assert preview["dry_run"] and preview["prices_changed"] == 1 and preview["to_out_of_stock"] == 1 and preview["unknown_in_feed_sample"] == ["SUP-UNKNOWN"]
    tea = await _sku_row(t, "TEA-1")
    assert tea.price == Decimal("9.00") and tea.availability == AVAIL      # nothing written yet

    done = await svc.run(t, sid, csv_text=FEED, dry_run=False, actor_id=uuid.uuid4())
    assert done["applied"] and done["to_out_of_stock"] == 1
    tea, rice = await _sku_row(t, "TEA-1"), await _sku_row(t, "RICE-1")
    assert tea.price == Decimal("10.50") and tea.supplier_cost == Decimal("7.00") and tea.availability == AVAIL
    assert rice.availability == OOS and rice.price == Decimal("4.50")      # cost unchanged: the price stays
    async with async_session_maker() as session:
        snaps = list((await session.execute(select(InventorySnapshot).where(InventorySnapshot.tenant_id == t, InventorySnapshot.source == "FEED"))).scalars())
        audit = list((await session.execute(select(AuditLog.action).where(AuditLog.tenant_id == t, AuditLog.action == "dropshipping.supplier.sync"))).scalars())
    assert sorted(s.quantity for s in snaps) == [0, 20] and len(audit) == 1
    supplier = (await catalog.list_suppliers(t))[0]
    assert supplier.last_sync_at is not None and supplier.last_sync_summary["to_out_of_stock"] == 1


async def test_a_feed_that_cannot_be_read_changes_nothing_and_is_remembered(catalog) -> None:
    t = uuid.uuid4()
    sid = await _shop(catalog, t)

    async def broken(url: str) -> str:
        raise FeedFetchError("The feed answered with status 503.")

    svc = DropshippingSyncService(async_session_maker, fetcher=broken)
    await svc.save_settings(t, sid, SETTINGS)
    with pytest.raises(CatalogValidationError) as err:
        await svc.run(t, sid, dry_run=False)
    assert "503" in err.value.problems[0]
    assert (await _sku_row(t, "TEA-1")).price == Decimal("9.00")
    supplier = (await catalog.list_suppliers(t))[0]
    assert supplier.last_sync_summary["applied"] is False and "503" in supplier.last_sync_summary["error"]
    with pytest.raises(CatalogValidationError):
        await DropshippingSyncService(async_session_maker).run(t, sid, csv_text="")  # an empty paste is refused too


async def test_the_daily_job_runs_only_the_suppliers_that_are_due(catalog) -> None:
    t = uuid.uuid4()
    due = await _shop(catalog, t)
    quiet = (await catalog.create_supplier(t, "Quiet Supply"))[0].id
    fetched: list[str] = []

    async def fetch(url: str) -> str:
        fetched.append(url)
        return FEED

    svc = DropshippingSyncService(async_session_maker, fetcher=fetch)
    await svc.save_settings(t, due, SETTINGS)
    await svc.save_settings(t, quiet, {**SETTINGS, "sync_enabled": False})
    assert await svc.run_due(t) == 1 and len(fetched) == 1
    assert await svc.run_due(t) == 0                    # just ran: not due for another 23 hours
    assert await svc.run_due(uuid.uuid4()) == 0         # another business has nothing


def test_only_public_https_feeds_are_fetched(monkeypatch) -> None:
    for bad in ("http://feeds.example.com/x.csv", "https://127.0.0.1/x.csv", "https://10.0.0.5/x.csv", "https://169.254.169.254/latest", "https://[::1]/x", "ftp://x.example/y"):
        with pytest.raises(FeedFetchError):
            _check_public_https(bad)
    monkeypatch.setattr(sync_mod.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    _check_public_https("https://feeds.example.com/x.csv")


# --- the staff routes ----------------------------------------------------------------------------------------------------------------------------


async def test_the_routes_need_manage_permission_and_default_to_a_preview(client) -> None:
    owner, _ = await _owner_with_catalog(client, "Sync Co", "sync-owner@example.com")
    sid = (await client.get(f"{BASE}/suppliers", headers=_h(owner))).json()["suppliers"][0]["id"]
    staff = await _user_token(client, owner, "sync-staff@example.com", Role.STAFF)
    for path, body in ((f"/suppliers/{sid}/sync-settings", SETTINGS), (f"/suppliers/{sid}/sync", {"csv_text": FEED})):
        assert (await (client.put if path.endswith("settings") else client.post)(f"{BASE}{path}", json=body, headers=_h(staff))).status_code == 403
        assert (await (client.put if path.endswith("settings") else client.post)(f"{BASE}{path}", json=body)).status_code == 401
    saved = await client.put(f"{BASE}/suppliers/{sid}/sync-settings", json={**SETTINGS, "feed_url": "http://x.example/f.csv"}, headers=_h(owner))
    assert saved.status_code == 422
    ok = await client.put(f"{BASE}/suppliers/{sid}/sync-settings", json=SETTINGS, headers=_h(owner))
    assert ok.status_code == 200 and ok.json()["sync"]["markup_percent"] == "50.00"
    feed = "supplier_sku,cost,quantity\nT-1,8.00,0\n"
    preview = await client.post(f"{BASE}/suppliers/{sid}/sync", json={"csv_text": feed}, headers=_h(owner))
    assert preview.status_code == 200 and preview.json()["dry_run"] is True
    assert (await client.get(f"{BASE}/suppliers", headers=_h(owner))).json()["suppliers"][0]["sync"]["last_sync_at"] is None  # a preview is not remembered
    assert (await client.post(f"{BASE}/suppliers/{uuid.uuid4()}/sync", json={"csv_text": feed}, headers=_h(owner))).status_code == 404
    assert (await client.post(f"{BASE}/suppliers/{sid}/sync", json={"csv_text": "nothing useful"}, headers=_h(owner))).status_code == 422
