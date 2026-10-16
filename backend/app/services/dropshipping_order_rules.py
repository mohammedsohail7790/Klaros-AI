"""Dropshipping store, branch 3: the rules for orders. Pure functions, no database.

The state machine, the ship-to rules for the supplier's reach (continental US only), contact and tracking validation, and the generators for order numbers
and the shopper's status token. Business-rule failures raise `CatalogValidationError` (a list of plain sentences), the same type the catalog uses.
"""

from __future__ import annotations

import re
import secrets

from app.models.dropshipping import OrderStatus
from app.services.customer_matching import normalize_phone
from app.services.dropshipping_catalog_import import CatalogValidationError

# The supplier ships to the continental United States only: the 48 contiguous states and DC. No Alaska, Hawaii or territories.
CONTIGUOUS_STATES = frozenset(
    "AL AZ AR CA CO CT DE DC FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TRACKING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{2,119}$")
_ORDER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: easy to read out over the phone
MAX_LINES = 50
MAX_QUANTITY = 99

# allowed moves; anything else is refused. CANCELLED and REFUND_REQUESTED end the automated flow.
TRANSITIONS: dict[str, frozenset[str]] = {
    OrderStatus.PENDING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.SUPPLIER_ORDER_PLACED, OrderStatus.REFUND_REQUESTED, OrderStatus.CANCELLED}),
    OrderStatus.SUPPLIER_ORDER_PLACED: frozenset({OrderStatus.SHIPPED, OrderStatus.REFUND_REQUESTED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED, OrderStatus.REFUND_REQUESTED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.REFUND_REQUESTED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REFUND_REQUESTED: frozenset(),
}
# an order's personal data may be erased once it is finished one way or the other
ERASABLE = frozenset({OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.REFUND_REQUESTED})


class OrderTransitionError(Exception):
    pass


def check_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise OrderTransitionError(f"An order that is {current} cannot become {target}.")


def new_order_number() -> str:
    return "DS-" + "".join(secrets.choice(_ORDER_ALPHABET) for _ in range(8))


def new_public_token() -> str:
    return secrets.token_urlsafe(32)


def _clean(value: object, field_name: str, *, max_len: int, required: bool = False) -> str | None:
    s = "" if value is None else str(value).strip()
    if not s:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if len(s) > max_len:
        raise ValueError(f"{field_name} is longer than {max_len} characters")
    if any(ord(c) < 32 for c in s):
        raise ValueError(f"{field_name} contains control characters")
    return s


def normalize_ship_to(raw: object) -> dict:
    """Validates a delivery address for the supplier's reach. Raises CatalogValidationError listing every problem."""
    raw = raw if isinstance(raw, dict) else {}
    problems: list[str] = []
    out: dict[str, str | None] = {}
    for key, max_len, required in (("name", 120, True), ("line1", 120, True), ("line2", 120, False), ("city", 80, True)):
        try:
            out[key] = _clean(raw.get(key), f"ship_to.{key}", max_len=max_len, required=required)
        except ValueError as exc:
            problems.append(str(exc))
    state = str(raw.get("state") or "").strip().upper()
    if state not in CONTIGUOUS_STATES:
        problems.append("ship_to.state must be one of the 48 contiguous states or DC (we do not ship to Alaska, Hawaii or US territories)")
    out["state"] = state
    postal = str(raw.get("postal_code") or "").strip()
    if not _ZIP_RE.match(postal):
        problems.append("ship_to.postal_code must be a 5-digit ZIP code (or ZIP+4)")
    out["postal_code"] = postal
    country = str(raw.get("country") or "US").strip().upper()
    if country not in ("US", "USA"):
        problems.append("ship_to.country must be US: we ship within the continental United States only")
    out["country"] = "US"
    if problems:
        raise CatalogValidationError(problems)
    return out


def normalize_contact(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    problems: list[str] = []
    out: dict[str, str | None] = {}
    try:
        out["name"] = _clean(raw.get("name"), "contact.name", max_len=120, required=True)
    except ValueError as exc:
        problems.append(str(exc))
    email = None
    try:
        email = _clean(raw.get("email"), "contact.email", max_len=255)
    except ValueError as exc:
        problems.append(str(exc))
    if email is not None and not _EMAIL_RE.match(email):
        problems.append("contact.email is not a valid address")
    phone = None
    try:
        phone = _clean(raw.get("phone"), "contact.phone", max_len=50)
    except ValueError as exc:
        problems.append(str(exc))
    if phone is not None and len(normalize_phone(phone) or "") != 10:
        problems.append("contact.phone must be a 10-digit US phone number")
    if email is None and phone is None:
        problems.append("contact needs an email or a phone number")
    out["email"], out["phone"] = (email.lower() if email else None), phone
    if problems:
        raise CatalogValidationError(problems)
    return out


def normalize_order_lines(raw: object) -> list[tuple[str, int]]:
    """[(sku reference, quantity)]; a reference is a SKU id or SKU code. The same SKU twice is merged, so a line is unique per SKU."""
    if not isinstance(raw, list) or not raw:
        raise CatalogValidationError(["An order needs at least one line."])
    if len(raw) > MAX_LINES:
        raise CatalogValidationError([f"An order may have at most {MAX_LINES} lines."])
    problems: list[str] = []
    merged: dict[str, int] = {}
    for i, line in enumerate(raw, start=1):
        line = line if isinstance(line, dict) else {}
        ref = str(line.get("sku") or line.get("sku_code") or line.get("sku_id") or "").strip()
        qty = line.get("quantity")
        if not ref:
            problems.append(f"line {i}: sku is required")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QUANTITY:
            problems.append(f"line {i}: quantity must be a whole number from 1 to {MAX_QUANTITY}")
        elif ref:
            merged[ref] = merged.get(ref, 0) + qty
    if any(q > MAX_QUANTITY for q in merged.values()):
        problems.append(f"the total quantity of one item may not exceed {MAX_QUANTITY}")
    if problems:
        raise CatalogValidationError(problems)
    return list(merged.items())


def normalize_tracking(carrier: object, number: object, url: object) -> tuple[str | None, str, str | None]:
    problems: list[str] = []
    carrier_s = None
    try:
        carrier_s = _clean(carrier, "carrier", max_len=80)
    except ValueError as exc:
        problems.append(str(exc))
    number_s = str(number or "").strip()
    if not _TRACKING_RE.match(number_s):
        problems.append("tracking_number must be 3-120 characters: letters, digits, space, dot, dash, slash or underscore")
    url_s = None
    try:
        url_s = _clean(url, "tracking_url", max_len=500)
    except ValueError as exc:
        problems.append(str(exc))
    if url_s is not None and not url_s.lower().startswith("https://"):
        problems.append("tracking_url must start with https://")
    if problems:
        raise CatalogValidationError(problems)
    return carrier_s, number_s, url_s


def normalize_reference(value: object, field_name: str = "payment_reference") -> str:
    s = "" if value is None else str(value).strip()
    if not s:
        raise CatalogValidationError([f"{field_name} is required"])
    if len(s) > 160 or any(ord(c) < 32 for c in s):
        raise CatalogValidationError([f"{field_name} must be at most 160 printable characters"])
    return s
