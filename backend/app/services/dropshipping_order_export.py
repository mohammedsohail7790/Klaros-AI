"""Dropshipping: the work queue as a spreadsheet the supplier can use. One row per item to order, with where it ships.

A plain CSV for staff only (it holds the shopper's delivery address, which the supplier needs to ship): order number, the supplier's own item code, quantity, item name and the
delivery address. A cell that starts with = + - or @ is prefixed so a spreadsheet program never runs it as a formula.
"""

from __future__ import annotations

import csv
import io
from typing import Any

COLUMNS = ["order_number", "supplier", "supplier_sku", "quantity", "item_name", "ship_to_name", "address_line1", "address_line2", "city", "state", "postal_code", "supplier_order_status"]


def _safe(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def supplier_orders_csv(entries: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for e in entries:
        ship = e.get("ship_to") or {}
        for item in e.get("items") or []:
            writer.writerow([_safe(v) for v in (
                e.get("order_number"), e.get("supplier"), item.get("supplier_sku") or item.get("sku_code"), item.get("quantity"), item.get("name"), ship.get("name"),
                ship.get("line1"), ship.get("line2"), ship.get("city"), ship.get("state"), ship.get("postal_code"), e.get("status"))])
    return out.getvalue()
