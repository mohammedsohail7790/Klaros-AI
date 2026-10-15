"""Dropshipping store, branch 2: validation of catalog data and parsing of a catalog CSV. Pure functions, no database.

Everything a person or an AI agent can submit about a product goes through `normalize_product`, so the staff API, the tools and the CSV import enforce the
same rules. The CSV side adds the file-level rules (size and row caps, known columns, formula-injection guard, duplicate codes) and the preview token that ties
a commit to exactly the file that was previewed.

Money is `Decimal`, a plain number with at most two decimals and no thousands separators. Only USD for now.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.core.config import get_settings
from app.models.dropshipping import SkuAvailability

MAX_CSV_CHARS = 1_000_000
MAX_CSV_ROWS = 2_000
CURRENCY = "USD"  # the only currency until a currency design exists

CSV_COLUMNS = (
    "sku_code", "product_name", "description", "category", "price", "currency", "supplier", "supplier_sku",
    "supplier_cost", "weight_grams", "availability", "non_perishable", "image_url", "product_slug",
)
REQUIRED_COLUMNS = ("sku_code", "product_name", "price", "supplier")

_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MONEY_RE = re.compile(r"^\d{1,12}(\.\d{1,2})?$")
_FORMULA_START = ("=", "+", "-", "@")
_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0", ""}


class CatalogValidationError(Exception):
    """One or more input rules failed. `problems` is a list of plain sentences; nothing was written."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class SkuSpec:
    sku_code: str
    price: Decimal
    supplier_ref: str  # a supplier id (UUID) or the supplier's exact name
    supplier_sku: str | None = None
    supplier_cost: Decimal | None = None
    currency: str = CURRENCY
    weight_grams: int | None = None
    availability: str = SkuAvailability.UNKNOWN.value


@dataclass(frozen=True)
class ProductSpec:
    slug: str
    name: str
    skus: tuple[SkuSpec, ...]
    description: str | None = None
    category: str | None = None
    image_url: str | None = None
    is_non_perishable: bool = False


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:160].strip("-")


def _text(value: object, field_name: str, *, max_len: int, required: bool = False) -> str | None:
    s = "" if value is None else str(value).strip()
    if not s:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if len(s) > max_len:
        raise ValueError(f"{field_name} is longer than {max_len} characters")
    if any(ord(c) < 32 and c not in "\n\t" for c in s):
        raise ValueError(f"{field_name} contains control characters")
    return s


def parse_money(value: object, field_name: str, *, allow_zero: bool = False) -> Decimal:
    s = "" if value is None else str(value).strip()
    if not _MONEY_RE.match(s):
        raise ValueError(f"{field_name} must be a plain number with at most 2 decimals (no currency symbol, no thousands separator)")
    amount = Decimal(s).quantize(Decimal("0.01"))
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError(f"{field_name} must be greater than 0")
    return amount


def parse_flag(value: object, field_name: str) -> bool:
    s = "" if value is None else str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"{field_name} must be yes or no")


def validate_image_url(value: object) -> str | None:
    url = _text(value, "image_url", max_len=500)
    if url is not None and not url.lower().startswith("https://"):
        raise ValueError("image_url must start with https://")
    return url


def normalize_sku(raw: dict) -> SkuSpec:
    code = _text(raw.get("sku_code"), "sku_code", max_len=80, required=True)
    if not _CODE_RE.match(code):
        raise ValueError("sku_code may only contain letters, digits, dot, dash and underscore, and must start with a letter or digit")
    supplier_ref = _text(raw.get("supplier"), "supplier", max_len=200, required=True)
    currency = (_text(raw.get("currency"), "currency", max_len=3) or CURRENCY).upper()
    if currency != CURRENCY:
        raise ValueError(f"currency must be {CURRENCY} for now")
    availability = (_text(raw.get("availability"), "availability", max_len=14) or SkuAvailability.UNKNOWN.value).upper()
    if availability not in {a.value for a in SkuAvailability}:
        raise ValueError("availability must be AVAILABLE, OUT_OF_STOCK or UNKNOWN")
    weight_raw = _text(raw.get("weight_grams"), "weight_grams", max_len=9)
    weight = None
    if weight_raw is not None:
        if not weight_raw.isdigit():
            raise ValueError("weight_grams must be a whole number of grams")
        weight = int(weight_raw)
    cost_raw = raw.get("supplier_cost")
    cost = parse_money(cost_raw, "supplier_cost", allow_zero=True) if cost_raw is not None and str(cost_raw).strip() != "" else None
    return SkuSpec(
        sku_code=code,
        price=parse_money(raw.get("price"), "price"),
        supplier_ref=supplier_ref,
        supplier_sku=_text(raw.get("supplier_sku"), "supplier_sku", max_len=120),
        supplier_cost=cost,
        currency=currency,
        weight_grams=weight,
        availability=availability,
    )


def normalize_product(raw: dict) -> ProductSpec:
    """Validates one product with its SKUs. Raises CatalogValidationError listing every problem found (not just the first)."""
    problems: list[str] = []

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            problems.append(str(exc))
            return None

    name = guard(_text, raw.get("name") or raw.get("product_name"), "name", max_len=255, required=True)
    slug_raw = _text(raw.get("slug") or raw.get("product_slug"), "slug", max_len=160)
    slug = slug_raw or (slugify(name) if name else "")
    if slug and not _SLUG_RE.match(slug):
        problems.append("slug may only contain lowercase letters, digits and single dashes")
    if not slug:
        problems.append("slug could not be derived from the name")
    description = guard(_text, raw.get("description"), "description", max_len=5000)
    category = guard(_text, raw.get("category"), "category", max_len=120)
    image_url = guard(validate_image_url, raw.get("image_url"))
    non_perishable = bool(guard(parse_flag, raw.get("is_non_perishable", raw.get("non_perishable")), "non_perishable"))

    skus: list[SkuSpec] = []
    seen: set[str] = set()
    for i, sku_raw in enumerate(raw.get("skus") or [], start=1):
        sku = guard(normalize_sku, sku_raw if isinstance(sku_raw, dict) else {})
        if sku is None:
            problems[-1] = f"sku {i}: {problems[-1]}"
            continue
        if sku.sku_code in seen:
            problems.append(f"sku {i}: {sku.sku_code} appears twice")
        seen.add(sku.sku_code)
        skus.append(sku)
    if not skus and not problems:
        problems.append("at least one SKU is required")
    if problems:
        raise CatalogValidationError(problems)
    return ProductSpec(slug=slug, name=name, skus=tuple(skus), description=description, category=category, image_url=image_url, is_non_perishable=non_perishable)


# --- the CSV ------------------------------------------------------------------------------------------------------------------------------------------


@dataclass
class ParsedRow:
    row: int  # 1 = the header, so the first data row is 2
    sku_code: str | None
    slug: str | None = None
    product_raw: dict = field(default_factory=dict)
    sku_raw: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def preview_token(tenant_id: uuid.UUID, csv_text: str) -> str:
    """Ties a commit to exactly the file that was previewed, for exactly this business. Stateless: recomputed, never stored."""
    digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    return hmac.new(get_settings().JWT_SECRET.encode("utf-8"), f"dropshipping-catalog-import:{tenant_id}:{digest}".encode("utf-8"), hashlib.sha256).hexdigest()


def token_matches(tenant_id: uuid.UUID, csv_text: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(preview_token(tenant_id, csv_text), token)


def parse_catalog_csv(csv_text: str) -> tuple[list[ParsedRow], list[str]]:
    """Returns (rows, file_problems). A file problem means nothing in the file can be used (too big, missing or unknown columns...). Row problems stay on their row."""
    if not isinstance(csv_text, str) or not csv_text.strip():
        return [], ["The file is empty."]
    if len(csv_text) > MAX_CSV_CHARS:
        return [], [f"The file is larger than {MAX_CSV_CHARS:,} characters."]
    try:
        table = list(csv.reader(io.StringIO(csv_text.lstrip("﻿"))))
    except csv.Error as exc:
        return [], [f"The file is not valid CSV: {exc}"]
    table = [r for r in table if any(c.strip() for c in r)]
    if not table:
        return [], ["The file is empty."]
    header = [h.strip().lower() for h in table[0]]
    problems: list[str] = []
    if len(set(header)) != len(header):
        problems.append("The header repeats a column name.")
    unknown = [h for h in header if h not in CSV_COLUMNS]
    if unknown:
        problems.append(f"Unknown column(s): {', '.join(sorted(set(unknown)))}.")
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        problems.append(f"Missing required column(s): {', '.join(missing)}.")
    if len(table) - 1 > MAX_CSV_ROWS:
        problems.append(f"The file has more than {MAX_CSV_ROWS:,} rows.")
    if problems:
        return [], problems

    rows: list[ParsedRow] = []
    first_row_of_slug: dict[str, ParsedRow] = {}
    seen_codes: dict[str, int] = {}
    for index, cells in enumerate(table[1:], start=2):
        parsed = ParsedRow(row=index, sku_code=None)
        rows.append(parsed)
        if len(cells) != len(header):
            parsed.problems.append(f"Expected {len(header)} columns, found {len(cells)}.")
            continue
        values = {h: c.strip() for h, c in zip(header, cells)}
        parsed.sku_code = values.get("sku_code") or None
        for col, value in values.items():
            if value.startswith(_FORMULA_START):
                parsed.problems.append(f"{col}: a cell may not start with = + - or @.")
        name = values.get("product_name", "")
        slug = values.get("product_slug") or slugify(name)
        parsed.slug = slug or None
        parsed.product_raw = {
            "name": name, "slug": slug, "description": values.get("description"), "category": values.get("category"),
            "image_url": values.get("image_url"), "non_perishable": values.get("non_perishable"),
        }
        parsed.sku_raw = {k: values.get(k) for k in ("sku_code", "supplier", "price", "currency", "supplier_sku", "supplier_cost", "weight_grams", "availability")}
        if parsed.sku_code:
            if parsed.sku_code in seen_codes:
                parsed.problems.append(f"sku_code {parsed.sku_code} already appears on row {seen_codes[parsed.sku_code]}.")
            else:
                seen_codes[parsed.sku_code] = index
        # rows with the same slug are variants of ONE product: the product fields must agree with the first row
        first = first_row_of_slug.get(slug) if slug else None
        if first is None:
            if slug:
                first_row_of_slug[slug] = parsed
        else:
            for key in ("name", "description", "category", "image_url", "non_perishable"):
                mine, theirs = (parsed.product_raw.get(key) or ""), (first.product_raw.get(key) or "")
                if mine and mine != theirs:
                    parsed.problems.append(f"{key} differs from row {first.row}, which uses the same product.")
    return rows, []
