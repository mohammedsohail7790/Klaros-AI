"""Dropshipping: the rules of the daily stock and price sync. Pure functions, no database and no network.

The feed is a CSV the supplier (or the supplier's portal) provides. Column names are matched loosely (see ALIASES). The sync only UPDATES SKUs that already exist and
carry the supplier's own code (`supplier_sku`); it never creates products, and a SKU that is simply absent from the feed is left exactly as it is (an incomplete feed must not
empty the shop). It can set three things per SKU: availability (from a quantity or an availability word), the supplier cost, and, when the supplier has a markup, the selling price.

Safeguards, because a broken feed must not damage the shop:
  - a price that would move by more than the supplier's `max_price_change_percent` is HELD for review, not applied;
  - if one run would flip more than half of at least ten in-stock SKUs to sold out, the availability changes are held (a truncated or zeroed feed looks exactly like that);
  - a feed that matches none of the supplier's SKUs applies nothing.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.models.dropshipping import SkuAvailability

MAX_FEED_CHARS = 2_000_000
MAX_FEED_ROWS = 20_000
MASS_SOLD_OUT_MIN = 10
MASS_SOLD_OUT_SHARE = Decimal("0.5")
SAMPLE = 20

ALIASES = {
    "id": ("supplier_sku", "sku", "item", "item_number", "item_no", "item_id", "product_id", "supplier_item"),
    "cost": ("cost", "supplier_cost", "wholesale", "wholesale_price", "unit_cost", "dealer_price", "price"),
    "quantity": ("quantity", "qty", "stock", "inventory", "available", "on_hand", "quantity_available", "qty_available"),
    "availability": ("availability", "status", "in_stock", "stock_status"),
}
_YES = {"available", "in stock", "in_stock", "instock", "yes", "y", "true", "1", "active"}
_NO = {"out of stock", "out_of_stock", "outofstock", "sold out", "soldout", "no", "n", "false", "0", "discontinued", "unavailable", "inactive"}


class FeedError(Exception):
    """The whole feed is unusable (the message says why)."""


@dataclass
class FeedRow:
    key: str  # the supplier's code, as written and upper-cased for matching
    cost: Decimal | None = None
    quantity: int | None = None
    availability: str | None = None  # SkuAvailability value, from a word, when there is no quantity


@dataclass
class FeedReport:
    rows: dict[str, FeedRow] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")


def _pick(headers: list[str], names: tuple[str, ...]) -> str | None:
    by_norm = {_norm(h): h for h in headers}
    for n in names:
        if n in by_norm:
            return by_norm[n]
    return None


def _money(text: str) -> Decimal:
    cleaned = text.strip().replace("$", "").replace(",", "").replace("USD", "").strip()
    value = Decimal(cleaned)  # raises InvalidOperation
    if not value.is_finite() or value < 0:
        raise InvalidOperation
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_feed(csv_text: str) -> FeedReport:
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise FeedError("The feed is empty.")
    if len(csv_text) > MAX_FEED_CHARS:
        raise FeedError(f"The feed is larger than {MAX_FEED_CHARS:,} characters.")
    text = csv_text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = list(reader.fieldnames or [])
    cols = {name: _pick(headers, names) for name, names in ALIASES.items()}
    if cols["id"] is None:
        raise FeedError("No column identifies the supplier's item code (expected one of: " + ", ".join(ALIASES["id"]) + ").")
    if cols["cost"] is None and cols["quantity"] is None and cols["availability"] is None:
        raise FeedError("The feed has no cost, quantity or availability column to sync.")
    report = FeedReport()
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_FEED_ROWS:
            raise FeedError(f"The feed has more than {MAX_FEED_ROWS:,} rows.")
        code = (raw.get(cols["id"]) or "").strip()
        if not code:
            continue
        key = code.upper()
        row = FeedRow(key=key)
        try:
            if cols["cost"] and (raw.get(cols["cost"]) or "").strip():
                row.cost = _money(raw[cols["cost"]])
            if cols["quantity"] and (raw.get(cols["quantity"]) or "").strip():
                row.quantity = int(Decimal(raw[cols["quantity"]].strip().replace(",", "")))
            if row.quantity is None and cols["availability"] and (raw.get(cols["availability"]) or "").strip():
                word = raw[cols["availability"]].strip().lower()
                if word in _YES:
                    row.availability = SkuAvailability.AVAILABLE.value
                elif word in _NO:
                    row.availability = SkuAvailability.OUT_OF_STOCK.value
                else:
                    raise ValueError(f"unknown availability {raw[cols['availability']]!r}")
        except (InvalidOperation, ValueError, ArithmeticError) as exc:
            report.problems.append(f"row {n} ({code}): {exc if isinstance(exc, ValueError) and str(exc) else 'a number could not be read'}")
            continue
        if key in report.rows:
            report.problems.append(f"row {n} ({code}): appears more than once; the first one is used")
            continue
        report.rows[key] = row
    if not report.rows:
        raise FeedError("The feed has no usable rows.")
    return report


def price_for(cost: Decimal, markup_percent: Decimal) -> Decimal:
    return (cost * (Decimal(1) + Decimal(markup_percent) / Decimal(100))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class SkuView:
    """What the planner needs to know about one SKU of the supplier."""
    id: str
    sku_code: str
    supplier_sku: str
    price: Decimal
    supplier_cost: Decimal | None
    availability: str


@dataclass
class Change:
    sku_id: str
    sku_code: str
    availability: str | None = None
    supplier_cost: Decimal | None = None
    price: Decimal | None = None
    quantity: int | None = None


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    matched: int = 0
    unchanged: int = 0
    not_in_feed: int = 0
    unknown_in_feed: list[str] = field(default_factory=list)
    unknown_in_feed_count: int = 0
    held_prices: list[dict] = field(default_factory=list)
    availability_held: bool = False
    to_available: int = 0
    to_out_of_stock: int = 0
    prices_changed: int = 0
    costs_changed: int = 0
    problems: list[str] = field(default_factory=list)
    applied: bool = False

    def summary(self) -> dict:
        return {
            "matched": self.matched, "unchanged": self.unchanged, "not_in_feed": self.not_in_feed, "unknown_in_feed": self.unknown_in_feed_count,
            "unknown_in_feed_sample": self.unknown_in_feed[:SAMPLE], "to_available": self.to_available, "to_out_of_stock": self.to_out_of_stock,
            "prices_changed": self.prices_changed, "costs_changed": self.costs_changed, "held_prices": self.held_prices[:SAMPLE],
            "held_price_count": len(self.held_prices), "availability_held": self.availability_held, "problems": self.problems[:SAMPLE], "applied": self.applied,
        }


def plan_sync(skus: list[SkuView], feed: FeedReport, *, markup_percent: Decimal | None, stock_buffer: int, max_price_change_percent: Decimal, force: bool = False) -> Plan:
    plan = Plan(problems=list(feed.problems))
    seen: set[str] = set()
    pending: list[tuple[Change, str | None]] = []  # (change, availability it would set), so the mass-sold-out guard can look at them together
    for sku in skus:
        key = (sku.supplier_sku or "").strip().upper()
        row = feed.rows.get(key) if key else None
        if row is None:
            plan.not_in_feed += 1
            continue
        seen.add(key)
        plan.matched += 1
        change = Change(sku_id=sku.id, sku_code=sku.sku_code)
        wanted = None
        if row.quantity is not None:
            wanted = SkuAvailability.AVAILABLE.value if row.quantity > stock_buffer else SkuAvailability.OUT_OF_STOCK.value
            change.quantity = row.quantity
        elif row.availability is not None:
            wanted = row.availability
        if wanted is not None and wanted != sku.availability:
            change.availability = wanted
        if row.cost is not None and row.cost != sku.supplier_cost:
            change.supplier_cost = row.cost
            plan.costs_changed += 1
        if row.cost is not None and markup_percent is not None:
            new_price = price_for(row.cost, markup_percent)
            if new_price > 0 and new_price != sku.price:
                move = abs(new_price - sku.price) / sku.price * 100 if sku.price > 0 else Decimal(0)
                if move > max_price_change_percent and not force:
                    plan.held_prices.append({"sku_code": sku.sku_code, "from": str(sku.price), "to": str(new_price), "change_percent": str(move.quantize(Decimal("0.1")))})
                else:
                    change.price = new_price
                    plan.prices_changed += 1
        pending.append((change, change.availability))
    feed_only = [k for k in feed.rows if k not in seen]
    plan.unknown_in_feed_count, plan.unknown_in_feed = len(feed_only), feed_only[:SAMPLE]
    if plan.matched == 0:
        plan.problems.append("None of the feed's codes match a SKU of this supplier, so nothing was changed.")
        return plan  # applied stays False, no changes
    in_stock_before = sum(1 for s in skus if (s.supplier_sku or "").strip().upper() in feed.rows and s.availability == SkuAvailability.AVAILABLE.value)
    flips = sum(1 for _, a in pending if a == SkuAvailability.OUT_OF_STOCK.value)
    if not force and in_stock_before >= MASS_SOLD_OUT_MIN and Decimal(flips) / Decimal(in_stock_before) > MASS_SOLD_OUT_SHARE:
        plan.availability_held = True
        plan.problems.append(f"{flips} of {in_stock_before} in-stock items would become sold out at once, which looks like a broken feed: stock changes were held. Check the feed, then run it again with 'force'.")
    for change, _ in pending:
        if plan.availability_held:
            change.availability = None
        if change.availability == SkuAvailability.AVAILABLE.value:
            plan.to_available += 1
        elif change.availability == SkuAvailability.OUT_OF_STOCK.value:
            plan.to_out_of_stock += 1
        if change.availability is not None or change.supplier_cost is not None or change.price is not None or change.quantity is not None:
            plan.changes.append(change)
        else:
            plan.unchanged += 1
    return plan
