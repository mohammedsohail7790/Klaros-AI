"""The supplier work queue as a CSV: one row per item to order with the delivery address, staff who work orders only, nothing a spreadsheet could run."""

import csv
import io

from app.models.rbac import Role
from app.services.dropshipping_order_export import COLUMNS, supplier_orders_csv
from tests.test_dropshipping_api import _h, _user_token
from tests.test_dropshipping_orders_api import BASE, _create, _owner_with_store


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_one_row_per_item_with_the_address_and_never_a_formula() -> None:
    entry = {"order_number": "DS-1", "supplier": "=cmd|' /C calc'!A0", "status": "TO_PLACE", "ship_to": {"name": "+Pat", "line1": "12 Main St", "line2": None, "city": "Austin", "state": "TX", "postal_code": "78701"},
             "items": [{"supplier_sku": "TEA-1-S", "sku_code": "TEA-1", "name": "Green Tea, loose", "quantity": 2}, {"supplier_sku": None, "sku_code": "RICE-1", "name": '@Rice "brown"', "quantity": 1}]}
    text = supplier_orders_csv([entry])
    rows = _rows(text)
    assert list(rows[0].keys()) == COLUMNS and len(rows) == 2
    assert rows[0]["supplier_sku"] == "TEA-1-S" and rows[0]["quantity"] == "2" and rows[0]["item_name"] == "Green Tea, loose" and rows[0]["city"] == "Austin" and rows[0]["address_line2"] == ""
    assert rows[1]["supplier_sku"] == "RICE-1"                                          # falls back to our own code when the supplier's is not recorded
    assert rows[0]["supplier"].startswith("'=") and rows[0]["ship_to_name"] == "'+Pat" and rows[1]["item_name"] == "'@Rice \"brown\""   # no cell can start with = + - @
    assert supplier_orders_csv([]).strip() == ",".join(COLUMNS)


async def test_the_route_gives_staff_the_queue_as_a_download_and_refuses_everyone_else(client) -> None:
    owner = await _owner_with_store(client, "Export Co", "export-co@example.com")
    staff = await _user_token(client, owner, "staff@export-co.example.com", Role.STAFF)
    viewer = await _user_token(client, owner, "viewer@export-co.example.com", Role.READ_ONLY)
    order = await _create(client, staff)
    assert (await client.post(f"{BASE}/orders/{order['id']}/mark-paid", json={"payment_reference": "BANK-7"}, headers=_h(owner))).status_code == 200

    r = await client.get(f"{BASE}/supplier-orders/export", headers=_h(staff))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and "supplier-orders-to_place.csv" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    rows = _rows(r.text)
    assert {x["order_number"] for x in rows} == {order["order_number"]} and {x["supplier_sku"] for x in rows} == {"TEA-1-S", "RICE-1-S"} and all(x["city"] == "Austin" for x in rows)

    assert (await client.get(f"{BASE}/supplier-orders/export?status=PLACED", headers=_h(staff))).text.strip() == ",".join(COLUMNS)   # nothing placed yet: header only
    assert (await client.get(f"{BASE}/supplier-orders/export?status=BOGUS", headers=_h(staff))).status_code == 422
    assert (await client.get(f"{BASE}/supplier-orders/export", headers=_h(viewer))).status_code == 403                                  # looking is allowed, a bulk address file is not
    assert (await client.get(f"{BASE}/supplier-orders/export")).status_code in (401, 403)
