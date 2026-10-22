"""Dropshipping store: shipping priced by the weight of the order, in tiers.

The supplier charges by parcel weight, so the store can charge the shopper the same way: a list of tiers, each with the heaviest order it covers (in pounds) and its fee, and last, optionally,
one open-ended tier for everything heavier. Example (US Direct's rates plus $1): up to 1 lb $6.95, up to 5 lb $10.95, ... , over 50 lb $53.95.

The weight of an order is the sum of each item's weight (grams, kept on the item) times its quantity. A tier's upper limit is inclusive ("up to 1 lb" includes exactly 1 lb). Pure functions,
no database; the settings code validates what the owner saves with `normalize_tiers`, and checkout prices an order with `tier_fee`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

GRAMS_PER_POUND = Decimal("453.59237")
MAX_TIERS = 20
MAX_LB = Decimal("1000")
MAX_FEE = Decimal("1000")


def _dec(value: object) -> Decimal | None:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None
    return d if d.is_finite() else None


def normalize_tiers(raw: object) -> tuple[list[dict], list[str]]:
    """Returns (tiers as stored: {"max_lb": "5" | None, "fee": "10.95"}, problems). Valid tiers have strictly increasing limits, and only the LAST may be open-ended (max_lb None)."""
    if not isinstance(raw, list) or not raw:
        return [], ["shipping_tiers must be a list with at least one tier"]
    if len(raw) > MAX_TIERS:
        return [], [f"shipping_tiers can have at most {MAX_TIERS} tiers"]
    out: list[dict] = []
    problems: list[str] = []
    previous = Decimal(0)
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            problems.append(f"tier {i} must be an object with max_lb and fee")
            continue
        fee = _dec(row.get("fee"))
        if fee is None or not Decimal(0) <= fee <= MAX_FEE:
            problems.append(f"tier {i}: fee must be an amount from 0 to {MAX_FEE}")
            continue
        limit_raw = row.get("max_lb")
        if limit_raw is None or limit_raw == "":
            if i != len(raw):
                problems.append(f"tier {i}: only the last tier can have no upper limit")
                continue
            out.append({"max_lb": None, "fee": str(fee.quantize(Decimal("0.01"), ROUND_HALF_UP))})
            continue
        limit = _dec(limit_raw)
        if limit is None or not Decimal(0) < limit <= MAX_LB:
            problems.append(f"tier {i}: max_lb must be a weight in pounds above 0 and at most {MAX_LB}")
            continue
        if limit <= previous:
            problems.append(f"tier {i}: max_lb must be larger than the tier before it")
            continue
        previous = limit
        out.append({"max_lb": str(int(limit)) if limit == limit.to_integral() else format(limit.normalize(), "f"), "fee": str(fee.quantize(Decimal("0.01"), ROUND_HALF_UP))})
    return ([] if problems else out), problems


def order_weight_lb(weights_and_quantities: list[tuple[int | None, int]]) -> Decimal | None:
    """Total weight in pounds, or None when any item has no weight (the order cannot be priced by weight)."""
    grams = 0
    for weight, qty in weights_and_quantities:
        if weight is None or weight <= 0:
            return None
        grams += weight * qty
    return Decimal(grams) / GRAMS_PER_POUND


def tier_fee(tiers: list[dict], weight_lb: Decimal) -> Decimal | None:
    """The fee of the first tier whose limit covers the weight; None when the order is heavier than every limited tier and there is no open-ended one."""
    for t in tiers:
        if t["max_lb"] is None or weight_lb <= Decimal(t["max_lb"]):
            return Decimal(t["fee"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return None


def describe(tiers: list[dict]) -> str:
    """For the line under the header: the cheapest tier and that it goes by weight."""
    if not tiers:
        return ""
    cheapest = min(Decimal(t["fee"]) for t in tiers)
    return f"Shipping from ${cheapest.quantize(Decimal('0.01'))}, by weight."
