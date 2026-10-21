"""Dropshipping store: the plain e-mails a shopper gets about their own order (payment received, on its way, delivered).

These are service messages about a purchase the shopper made, not marketing: no promotions, no tracking pixels. They go out through the system mailer, which sends
only when a real e-mail provider is configured and otherwise says so in the log; a message that cannot be sent never undoes or delays the order's own state change
(the caller has already committed it), and an erased order (no e-mail address) sends nothing. The text states only facts the order holds.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from app.core.config import get_settings
from app.models.dropshipping import Order
from app.services.system_mailer import send_system_email

logger = structlog.get_logger(__name__)

PAID, SHIPPED, DELIVERED = "paid", "shipped", "delivered"


def _money(value: object, currency: str) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))} {currency}"


def _lines(order: dict) -> str:
    return "\n".join(f"  {ln['quantity']} x {ln['name']}: {_money(ln['line_total'], order['currency'])}" for ln in order["lines"])


def _parcels(order: dict) -> str:
    rows = []
    for so in order["supplier_orders"]:
        for sh in so["shipments"]:
            label = " ".join(p for p in (sh.get("carrier"), sh.get("tracking_number")) if p) or "Tracking will follow"
            rows.append(f"  {label}" + (f"\n  {sh['tracking_url']}" if sh.get("tracking_url") else ""))
    return "\n".join(rows)


def build_order_email(event: str, order: dict, *, store_name: str, status_url: str) -> tuple[str, str]:
    """Returns (subject, body). `order` is the dict the order service returns (staff shape, with the lines and shipments)."""
    number, name = order["order_number"], (order.get("ship_to") or {}).get("name") or ""
    hello = f"Hello {name}," if name else "Hello,"
    follow = f"You can follow your order here:\n{status_url}"
    if event == PAID:
        subject = f"{store_name}: we have your order {number}"
        body = (f"{hello}\n\nThank you for your order. We have received your payment and are preparing it.\n\nOrder {number}\n{_lines(order)}\n"
                f"  Shipping: {_money(order['shipping'], order['currency'])}\n  Total: {_money(order['total'], order['currency'])}\n\n{follow}\n\n{store_name}\n")
    elif event == SHIPPED:
        subject = f"{store_name}: your order {number} is on its way"
        body = f"{hello}\n\nYour order {number} has been shipped.\n\n{_parcels(order)}\n\n{follow}\n\n{store_name}\n"
    elif event == DELIVERED:
        subject = f"{store_name}: your order {number} was delivered"
        body = f"{hello}\n\nYour order {number} has been marked as delivered. If anything is wrong with it, just reply to this e-mail.\n\n{follow}\n\n{store_name}\n"
    else:
        raise ValueError(f"unknown order e-mail event {event!r}")
    return subject, body


async def notify_order(event: str, order: Order, view: dict) -> bool:
    """Best effort, never raises. Returns True only when the provider accepted the message."""
    try:
        to = (order.contact_email or "").strip()
        if not to or order.personal_data_erased_at is not None:
            return False
        from app.services.dropshipping_checkout_service import store_base_url  # imported late: that module imports the order service

        store_name = get_settings().DROPSHIPPING_STORE_NAME
        subject, body = build_order_email(event, view, store_name=store_name, status_url=f"{store_base_url()}/store/orders/{order.public_token}")
        sent = await send_system_email(to=to, subject=subject, body=body, from_name=store_name)
        logger.info("dropshipping_order_email", kind=event, order_number=order.order_number, sent=sent)
        return sent
    except Exception as exc:  # noqa: BLE001
        logger.warning("dropshipping_order_email_failed", kind=event, error_type=type(exc).__name__)
        return False
