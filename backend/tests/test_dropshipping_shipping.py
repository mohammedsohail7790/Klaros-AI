"""Dropshipping store: shipping priced by the weight of the order, in tiers. The pure rules, the settings and the open gate, and what checkout charges."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.db.session import async_session_maker
from app.models.dropshipping import Sku
from app.services import dropshipping_shipping as ship
from tests.test_dropshipping_api import _h
from tests.test_dropshipping_orders_api import BASE
from tests.test_dropshipping_store_api import STORE, _checkout, _live_store, _order_row, _shop_body, stripe  # noqa: F401 - `stripe` is a fixture

# US Direct's rates plus $1 each (Hk, 21 October): the owner's own tiers, as a customer is charged
HK = [("1", "6.95"), ("5", "10.95"), ("10", "15.95"), ("15", "20.95"), ("20", "25.95"), ("25", "27.95"), ("30", "29.95"), ("40", "33.95"), ("50", "43.95"), (None, "53.95")]
TIERS = [{"max_lb": lb, "fee": fee} for lb, fee in HK]


def test_the_owners_tiers_are_accepted_and_stored_tidily() -> None:
    tiers, problems = ship.normalize_tiers([{"max_lb": 1, "fee": 6.95}, {"max_lb": "5.0", "fee": "10.9"}, {"max_lb": None, "fee": "53.95"}])
    assert problems == [] and tiers == [{"max_lb": "1", "fee": "6.95"}, {"max_lb": "5", "fee": "10.90"}, {"max_lb": None, "fee": "53.95"}]
    assert ship.normalize_tiers(TIERS)[1] == []


@pytest.mark.parametrize("raw", [None, [], "tiers", [{"max_lb": "1"}], [{"max_lb": "0", "fee": "1"}], [{"max_lb": "-2", "fee": "1"}], [{"max_lb": "abc", "fee": "1"}], [{"max_lb": "1", "fee": "-1"}],
                                [{"max_lb": "5", "fee": "1"}, {"max_lb": "5", "fee": "2"}], [{"max_lb": "5", "fee": "1"}, {"max_lb": "3", "fee": "2"}],
                                [{"max_lb": None, "fee": "9"}, {"max_lb": "5", "fee": "1"}], [{"max_lb": "9999", "fee": "1"}], ["x"], [{"max_lb": str(i + 1), "fee": "1"} for i in range(21)]])
def test_bad_tiers_are_refused_with_a_reason(raw) -> None:
    tiers, problems = ship.normalize_tiers(raw)
    assert tiers == [] and problems


def test_a_tiers_upper_limit_is_included_and_the_heaviest_tier_catches_the_rest() -> None:
    tiers, _ = ship.normalize_tiers(TIERS)
    fee = lambda lb: ship.tier_fee(tiers, Decimal(str(lb)))  # noqa: E731
    assert fee("0.2") == Decimal("6.95") and fee("1") == Decimal("6.95")        # exactly 1 lb is still the first tier
    assert fee("1.01") == Decimal("10.95") and fee("5") == Decimal("10.95") and fee("5.01") == Decimal("15.95")
    assert fee("50") == Decimal("43.95") and fee("50.01") == Decimal("53.95") and fee("400") == Decimal("53.95")
    capped, _ = ship.normalize_tiers([{"max_lb": "10", "fee": "5"}])
    assert ship.tier_fee(capped, Decimal("10")) == Decimal("5.00") and ship.tier_fee(capped, Decimal("10.01")) is None   # nothing covers it: it cannot be shipped


def test_the_weight_of_an_order_is_summed_in_pounds_and_unknown_if_any_item_has_none() -> None:
    assert ship.order_weight_lb([(454, 1)]).quantize(Decimal("0.001")) == Decimal("1.001")
    assert ship.order_weight_lb([(300, 2), (500, 1)]).quantize(Decimal("0.01")) == Decimal("2.43")
    assert ship.order_weight_lb([(300, 2), (None, 1)]) is None and ship.order_weight_lb([(0, 1)]) is None
    assert ship.describe(ship.normalize_tiers(TIERS)[0]) == "Shipping from $6.95, by weight."


async def _weigh(tenant_id: uuid.UUID, grams: dict[str, int | None]) -> None:
    async with async_session_maker() as session:
        for code, g in grams.items():
            await session.execute(update(Sku).where(Sku.tenant_id == tenant_id, Sku.sku_code == code).values(weight_grams=g))
        await session.commit()


async def _tiered_store(client, monkeypatch, org: str, email: str, **extra):
    owner, tenant_id = await _live_store(client, monkeypatch, org, email)
    r = await client.put(f"{BASE}/store-settings", json={"shipping_tiers": TIERS, **extra}, headers=_h(owner))
    assert r.status_code == 200, r.text
    return owner, tenant_id, r.json()


async def test_the_store_cannot_open_by_weight_until_every_item_has_a_weight(client, monkeypatch, stripe) -> None:
    owner, tenant_id = await _live_store(client, monkeypatch, "Weightless Co", "weightless@example.com")
    r = await client.put(f"{BASE}/store-settings", json={"shipping_tiers": TIERS}, headers=_h(owner))
    assert r.status_code == 200 and r.json()["open_to_shoppers"] is False
    assert any("no weight" in b for b in r.json()["blockers"])
    assert (await client.get(STORE)).status_code == 404                                   # closed to shoppers meanwhile
    await _weigh(tenant_id, {"TEA-1": 300, "RICE-1": 500})
    view = await client.get(f"{BASE}/store-settings", headers=_h(owner))
    assert view.json()["open_to_shoppers"] is True and view.json()["blockers"] == []


async def test_checkout_charges_the_tier_for_the_weight_of_the_order(client, monkeypatch, stripe) -> None:
    owner, tenant_id, _ = await _tiered_store(client, monkeypatch, "Tier Checkout Co", "tier-checkout@example.com")
    await _weigh(tenant_id, {"TEA-1": 300, "RICE-1": 500})                              # 2 x 300 g + 500 g = 1100 g = 2.43 lb: the up-to-5-lb tier
    out = await _checkout(client)
    order = await _order_row(out["order_number"])
    assert Decimal(str(order.shipping)) == Decimal("10.95") and out["total"] == "40.95"   # 30.00 of goods + 10.95
    await _weigh(tenant_id, {"TEA-1": 60, "RICE-1": 80})                                # 200 g: the first tier
    light = await _order_row((await _checkout(client))["order_number"])
    assert Decimal(str(light.shipping)) == Decimal("6.95")
    await _weigh(tenant_id, {"TEA-1": 6000, "RICE-1": 8000})                            # 20 kg = 44 lb: the up-to-50-lb tier
    heavy = await _order_row((await _checkout(client))["order_number"])
    assert Decimal(str(heavy.shipping)) == Decimal("43.95")


async def test_free_shipping_over_a_threshold_still_applies_to_tiers(client, monkeypatch, stripe) -> None:
    owner, tenant_id, _ = await _tiered_store(client, monkeypatch, "Tier Free Co", "tier-free@example.com", free_shipping_over="30.00")
    await _weigh(tenant_id, {"TEA-1": 300, "RICE-1": 500})
    order = await _order_row((await _checkout(client))["order_number"])                  # 30.00 of goods reaches the threshold
    assert Decimal(str(order.shipping)) == 0


async def test_an_order_with_an_item_that_has_no_weight_or_too_heavy_is_refused_politely(client, monkeypatch, stripe) -> None:
    owner, tenant_id, _ = await _tiered_store(client, monkeypatch, "Tier Refuse Co", "tier-refuse@example.com")
    await _weigh(tenant_id, {"TEA-1": 300, "RICE-1": 500})
    await client.put(f"{BASE}/store-settings", json={"shipping_tiers": [{"max_lb": "2", "fee": "5.00"}]}, headers=_h(owner))   # nothing covers more than 2 lb
    r = await client.post(f"{STORE}/checkout", json=_shop_body())
    assert r.status_code == 422 and "more than the store ships in one order" in r.json()["detail"]["problems"][0]
    await _weigh(tenant_id, {"RICE-1": None})                                          # the store closes itself too: an item lost its weight
    assert (await client.get(STORE)).status_code == 404


async def test_the_shop_shows_the_tiers_and_each_items_weight(client, monkeypatch, stripe) -> None:
    owner, tenant_id, _ = await _tiered_store(client, monkeypatch, "Tier Public Co", "tier-public@example.com")
    await _weigh(tenant_id, {"TEA-1": 300, "RICE-1": 500})
    info = (await client.get(STORE)).json()
    assert info["shipping"] == "Shipping from $6.95, by weight. Free on orders of $50.00 or more." and info["shipping_tiers"][0] == {"max_lb": "1", "fee": "6.95"} and info["shipping_tiers"][-1] == {"max_lb": None, "fee": "53.95"}
    items = [i for p in (await client.get(f"{STORE}/products")).json()["products"] for i in p["items"]]
    assert sorted(i["weight_g"] for i in items) == [300, 500]


async def test_clearing_the_tiers_goes_back_to_the_flat_fee(client, monkeypatch, stripe) -> None:
    owner, tenant_id, _ = await _tiered_store(client, monkeypatch, "Tier Clear Co", "tier-clear@example.com")
    cleared = await client.put(f"{BASE}/store-settings", json={"shipping_tiers": None}, headers=_h(owner))
    assert cleared.status_code == 200 and cleared.json()["settings"]["shipping_tiers"] is None
    assert (await client.get(STORE)).json()["shipping"].startswith("Flat-rate shipping: $5.00")
    bad = await client.put(f"{BASE}/store-settings", json={"shipping_tiers": [{"max_lb": "5", "fee": "1"}, {"max_lb": "3", "fee": "2"}]}, headers=_h(owner))
    assert bad.status_code == 422 and any("larger than the tier before" in p for p in bad.json()["detail"]["problems"])
