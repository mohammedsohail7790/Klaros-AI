"""Dropshipping store: sales tax through Stripe Tax.

How it works. When the store's tax mode is STRIPE_TAX, checkout sends Stripe one line per product bought (tax-exclusive, with the store's food tax code) and a shipping line (Stripe's
shipping tax code), together with a Stripe Customer carrying the shopper's delivery address. Stripe Tax works out the tax of that address and shows it on its own page. The payment
that comes back is then LARGER than the order's total; `verify_tax` is what lets the webhook accept that difference (and only that difference): the Checkout Session is read back from
Stripe and must agree with the order on the pre-tax amount, with the payment on the amount charged, and the tax must be plausible. Anything else stays a mismatch for staff to review.

Two things are Stripe's, not ours: Stripe Tax must be active on the account, and it only charges tax in places where the account has an active registration. `check_ready` asks Stripe
about both when the owner turns the mode on, so the owner is told instead of silently under-collecting.

Tax codes (checked against Stripe's product tax code list): txcd_40040000 is "Food for Non-Immediate Consumption" (groceries bought to take home); txcd_40060003 is the code for
food for IMMEDIATE consumption (prepared food) and must not be used here. txcd_92010001 is Stripe's shipping code. Candy, soft drinks and dietary supplements have their own codes
and are taxed differently in many states; one store-wide code does not cover them (see the notes on the pull request).
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.integrations.stripe_client import StripeAPIError, StripeClient

MODE_NONE, MODE_STRIPE_TAX = "NONE", "STRIPE_TAX"
MODES = (MODE_NONE, MODE_STRIPE_TAX)
FOOD_TAX_CODE = "txcd_40040000"
SHIPPING_TAX_CODE = "txcd_92010001"
TAX_CODE_RE = re.compile(r"^txcd_\d{8}$")
MAX_TAX_SHARE = Decimal("0.25")  # US combined state, county and city sales tax stays far below this: a bigger figure is not believed


def cents(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).to_integral_value())


def build_line_items(lines: list[tuple[str, Decimal, int]], shipping: Decimal, tax_code: str) -> list[dict]:
    """One entry per product line, then the shipping line when there is a shipping charge. `lines` = (name, unit price, quantity). Money in cents, tax-exclusive."""
    items = [{"name": name[:250], "unit_amount": cents(price), "quantity": int(qty), "tax_code": tax_code} for name, price, qty in lines]
    if Decimal(shipping) > 0:
        items.append({"name": "Shipping", "unit_amount": cents(shipping), "quantity": 1, "tax_code": SHIPPING_TAX_CODE})
    return items


def verify_tax(session: dict, *, payment_intent_id: str, received: Decimal, pre_tax_total: Decimal) -> tuple[Decimal | None, str | None]:
    """(tax, None) when the payment is exactly the order's pre-tax total plus a believable tax that Stripe itself reports for this Checkout Session; otherwise (None, reason)."""
    if session.get("payment_intent") != payment_intent_id:
        return None, "the Checkout Session does not belong to this payment"
    details = session.get("total_details") or {}
    try:
        tax, subtotal, total = int(details.get("amount_tax")), int(session.get("amount_subtotal")), int(session.get("amount_total"))
    except (TypeError, ValueError):
        return None, "Stripe did not report the tax of this payment"
    if total != cents(received):
        return None, "the amount Stripe reports for the session differs from the amount received"
    if subtotal != cents(pre_tax_total):
        return None, "the session's pre-tax amount does not match the order"
    if tax <= 0 or total - tax != subtotal:
        return None, "the tax Stripe reports does not add up"
    if Decimal(tax) > Decimal(subtotal) * MAX_TAX_SHARE:
        return None, "the tax is implausibly large"
    return Decimal(tax) / 100, None


async def check_ready(secret_key: str) -> list[str]:
    """Plain sentences for what is missing before Stripe can collect tax; an empty list means ready. Read-only calls to Stripe."""
    client = StripeClient(secret_key)
    problems: list[str] = []
    try:
        settings = await client.get_tax_settings()
        if settings.get("status") != "active":
            problems.append("Stripe Tax is not active on your Stripe account yet: finish its setup in the Stripe Dashboard (Tax > Settings).")
        registrations = (await client.list_active_tax_registrations()).get("data") or []
        if not registrations:
            problems.append("Your Stripe account has no active tax registration. Stripe only collects tax where you are registered: add your registrations in the Stripe Dashboard (Tax > Registrations).")
    except StripeAPIError as exc:
        problems.append(f"Stripe could not be asked about Stripe Tax ({exc}). Check that the Stripe key is allowed to read Tax settings.")
    return problems
