"""Dropshipping store: a plain reminder for a shopper who started an order and did not pay.

At most two e-mails per order: one an hour after the order was started and one a day after it was started. Nothing for an order older than three days, for an order that was
paid or cancelled in the meantime, for an erased order (no e-mail address), or while the store is closed (the pay link would not work). It is a service message about the
shopper's own order, not marketing: the basket as it was, the total, the link to the order page where the Pay button is, and no promotion. A message the provider did not
accept is not counted, so it is tried again on the next pass; a message that cannot be sent never changes the order.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.dropshipping import Order, OrderLine, OrderStatus
from app.services.system_mailer import send_system_email

logger = structlog.get_logger(__name__)

# The wait after the order was started before reminder number 1 and number 2.
DELAYS = (timedelta(hours=1), timedelta(hours=24))
GIVE_UP_AFTER = timedelta(days=3)


def _money(value: object, currency: str) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))} {currency}"


def build_reminder_email(number: int, order: Order, lines: list[OrderLine], *, store_name: str, status_url: str) -> tuple[str, str]:
    """Returns (subject, body) for reminder `number` (1 or 2)."""
    name = ((order.ship_to or {}).get("name") or "").strip()
    hello = f"Hello {name}," if name else "Hello,"
    basket = "\n".join(f"  {ln.quantity} x {ln.name_snapshot}: {_money(ln.line_total, order.currency)}" for ln in lines)
    closing = "This is the last reminder we will send about this order." if number >= 2 else "If you have already paid, or no longer want it, please ignore this e-mail."
    subject = f"{store_name}: your order {order.order_number} is waiting for payment"
    body = (f"{hello}\n\nYou started an order with us and it has not been paid yet. Your basket is saved:\n\n{basket}\n"
            f"  Shipping: {_money(order.shipping, order.currency)}\n  Total: {_money(order.total, order.currency)}\n\n"
            f"You can finish paying on your order page:\n{status_url}\n\n{closing}\n\n{store_name}\n")
    return subject, body


def is_due(order: Order, now: datetime) -> bool:
    n = order.payment_reminders_sent or 0
    if n >= len(DELAYS) or order.status != OrderStatus.PENDING_PAYMENT.value:
        return False
    placed = order.placed_at if order.placed_at.tzinfo else order.placed_at.replace(tzinfo=timezone.utc)
    if now - placed > GIVE_UP_AFTER:
        return False
    return now >= placed + DELAYS[n]


async def send_due_reminders(session_factory, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """One pass: sends every reminder that is due and returns how many the provider accepted. Never raises."""
    if not get_settings().DROPSHIPPING_PAYMENT_REMINDERS:
        return 0
    now = now or datetime.now(timezone.utc)
    sent_count = 0
    try:
        from app.api.tool_deps_integrations import get_integration_connection_service  # late: the API layer imports the services
        from app.services.dropshipping_checkout_service import store_base_url
        from app.services.dropshipping_store_service import DropshippingStoreService

        if not (await DropshippingStoreService(session_factory, get_integration_connection_service()).is_open(tenant_id))[0]:
            return 0
        store_name = get_settings().DROPSHIPPING_STORE_NAME
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            candidates = (await session.execute(select(Order).where(
                Order.tenant_id == tenant_id, Order.status == OrderStatus.PENDING_PAYMENT.value, Order.contact_email.is_not(None), Order.personal_data_erased_at.is_(None),
                Order.payment_reminders_sent < len(DELAYS), Order.placed_at >= now - GIVE_UP_AFTER).order_by(Order.placed_at).limit(200))).scalars().all()
            for order in candidates:
                if not is_due(order, now):
                    continue
                lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order.id).order_by(OrderLine.created_at))).scalars())
                number = (order.payment_reminders_sent or 0) + 1
                subject, body = build_reminder_email(number, order, lines, store_name=store_name, status_url=f"{store_base_url()}/store/orders/{order.public_token}")
                try:
                    ok = await send_system_email(to=order.contact_email.strip(), subject=subject, body=body, from_name=store_name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("dropshipping_payment_reminder_failed", error_type=type(exc).__name__)
                    ok = False
                if ok:
                    order.payment_reminders_sent = number
                    order.last_payment_reminder_at = now
                    await session.commit()
                    sent_count += 1
                    logger.info("dropshipping_payment_reminder", order_number=order.order_number, number=number)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dropshipping_payment_reminders_failed", error_type=type(exc).__name__)
    return sent_count


async def run_payment_reminder_sweeper(session_factory, tenant_id_getter, stop: "asyncio.Event", every_seconds: float = 900.0) -> None:
    """Background loop: every 15 minutes, sends the reminders that have become due. The count kept on each order makes a restart or a second server harmless."""
    while not stop.is_set():
        try:
            tenant_id = tenant_id_getter()
            if tenant_id is not None:
                await send_due_reminders(session_factory, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dropshipping_payment_reminder_sweep_failed", error_type=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_seconds)
        except asyncio.TimeoutError:
            pass
