"""Dropshipping: the store's morning e-mail to its owners. How yesterday went (orders, revenue), what is waiting on a person, and which items are sold out or running low.

Built from the store's own tables, goes only to the active OWNER users of the store's business (through the internal channel, like the Medical Tourism owner digest), at most once a
day, and is skipped (not sent empty) on a quiet day: no orders, nothing waiting and nothing sold out. It carries no customer names, e-mail addresses or addresses: only order numbers,
counts, money and product names, with links back into the staff app.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.dropshipping import (
    InventorySnapshot,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    ProductStatus,
    Shipment,
    ShipmentStatus,
    Sku,
    SkuAvailability,
    SkuStatus,
    SupplierOrder,
    SupplierOrderStatus,
)
from app.models.organization import Organization
from app.models.rbac import Role
from app.models.user import User

logger = structlog.get_logger(__name__)

SENT_ACTION = "dropshipping.digest_sent"
LOW_STOCK_AT = 5          # a known quantity from 1 to this counts as running low
STUCK_AFTER = timedelta(hours=48)  # paid, and still not shipped after this long
MAX_NAMES = 10


def _money(value: Decimal | None) -> str:
    return f"${(value or Decimal(0)).quantize(Decimal('0.01'))}"


def margin_of(rows, since: datetime) -> dict[str, Any] | None:
    """An ESTIMATE of what the paid product lines since `since` earned: price paid minus today's supplier cost, before shipping, tax and refunds. Lines whose item has no supplier cost
    are left out and counted, so the figure is never made up. None when no line has a cost."""
    sales = cost = Decimal(0)
    uncosted = 0
    for line_total, quantity, supplier_cost, paid_at in rows:
        at = paid_at if paid_at.tzinfo else paid_at.replace(tzinfo=timezone.utc)
        if at < since:
            continue
        if supplier_cost is None:
            uncosted += 1
            continue
        sales += Decimal(line_total)
        cost += Decimal(supplier_cost) * int(quantity)
    if sales == 0:
        return None
    return {"sales": sales, "margin": sales - cost, "percent": ((sales - cost) / sales * 100).quantize(Decimal("0.1")), "uncosted_lines": uncosted}


async def gather(session_factory, tenant_id: uuid.UUID, now: datetime | None = None) -> dict[str, Any]:
    """The numbers. Pure reads, tenant-scoped."""
    now = now or datetime.now(timezone.utc)
    day, week = now - timedelta(hours=24), now - timedelta(days=7)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)

        async def count(stmt) -> int:
            return int((await session.execute(stmt)).scalar_one() or 0)

        paid = Order.paid_at.is_not(None) & (Order.status != OrderStatus.CANCELLED.value)
        orders_24h = await count(select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id, Order.placed_at >= day))
        paid_24h = await count(select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id, paid, Order.paid_at >= day))
        revenue_24h = (await session.execute(select(func.coalesce(func.sum(Order.total), 0)).where(Order.tenant_id == tenant_id, paid, Order.paid_at >= day))).scalar_one()
        revenue_7d = (await session.execute(select(func.coalesce(func.sum(Order.total), 0)).where(Order.tenant_id == tenant_id, paid, Order.paid_at >= week))).scalar_one()
        margin_rows = (await session.execute(select(OrderLine.line_total, OrderLine.quantity, Sku.supplier_cost, Order.paid_at).join(Order, Order.id == OrderLine.order_id).join(
            Sku, Sku.id == OrderLine.sku_id).where(Order.tenant_id == tenant_id, paid, Order.paid_at >= week))).all()
        margin_24h, margin_7d = margin_of(margin_rows, day), margin_of(margin_rows, week)
        waiting_to_order = await count(select(func.count()).select_from(SupplierOrder).where(SupplierOrder.tenant_id == tenant_id, SupplierOrder.status == SupplierOrderStatus.TO_PLACE.value))
        failed_supplier = await count(select(func.count()).select_from(SupplierOrder).where(SupplierOrder.tenant_id == tenant_id, SupplierOrder.status == SupplierOrderStatus.FAILED.value))
        refunds = (await session.execute(select(Order.order_number).where(Order.tenant_id == tenant_id, Order.status == OrderStatus.REFUND_REQUESTED.value).order_by(Order.placed_at).limit(MAX_NAMES))).scalars().all()
        exceptions = await count(select(func.count()).select_from(Shipment).where(Shipment.tenant_id == tenant_id, Shipment.status == ShipmentStatus.EXCEPTION.value))
        stuck = (await session.execute(select(Order.order_number).where(
            Order.tenant_id == tenant_id, Order.status.in_([OrderStatus.PAID.value, OrderStatus.SUPPLIER_ORDER_PLACED.value]), Order.paid_at < now - STUCK_AFTER).order_by(Order.paid_at).limit(MAX_NAMES))).scalars().all()

        rows = (await session.execute(select(Sku.id, Sku.sku_code, Sku.availability, Product.name).join(Product, Product.id == Sku.product_id).where(
            Sku.tenant_id == tenant_id, Sku.status == SkuStatus.ACTIVE.value, Product.status == ProductStatus.ACTIVE.value).limit(5000))).all()
        sold_out = [f"{name} ({code})" for _id, code, availability, name in rows if availability == SkuAvailability.OUT_OF_STOCK.value]
        latest: dict[uuid.UUID, int | None] = {}
        if rows:
            snaps = (await session.execute(select(InventorySnapshot.sku_id, InventorySnapshot.quantity).where(
                InventorySnapshot.tenant_id == tenant_id, InventorySnapshot.sku_id.in_([r[0] for r in rows])).order_by(InventorySnapshot.observed_at.desc()))).all()
            for sku_id, qty in snaps:
                latest.setdefault(sku_id, qty)  # newest first: the first seen is the latest
        low = [f"{name} ({code}): {latest[sid]} left" for sid, code, availability, name in rows
               if availability == SkuAvailability.AVAILABLE.value and latest.get(sid) is not None and 1 <= latest[sid] <= LOW_STOCK_AT]
        org = await session.get(Organization, tenant_id)
        owners = [u.email for u in (await session.execute(select(User).where(User.tenant_id == tenant_id, User.role == Role.OWNER, User.is_active.is_(True)))).scalars() if u.email]
        done = await count(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == SENT_ACTION,
                                                                            AuditLog.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)))
    return {
        "business": org.name if org is not None else "Your store", "owners": owners, "already_sent_today": bool(done),
        "orders_24h": orders_24h, "paid_24h": paid_24h, "revenue_24h": Decimal(revenue_24h), "revenue_7d": Decimal(revenue_7d),
        "waiting_to_order": waiting_to_order, "failed_supplier": failed_supplier, "refunds": list(refunds), "exceptions": exceptions, "stuck": list(stuck),
        "sold_out": sold_out, "low": low, "margin_24h": margin_24h, "margin_7d": margin_7d,
    }


def needs_attention(d: dict[str, Any]) -> bool:
    return bool(d["waiting_to_order"] or d["failed_supplier"] or d["refunds"] or d["exceptions"] or d["stuck"] or d["sold_out"] or d["low"])


def is_quiet(d: dict[str, Any]) -> bool:
    return d["orders_24h"] == 0 and d["paid_24h"] == 0 and not needs_attention(d)


def compose(d: dict[str, Any], base_url: str) -> tuple[str, str]:
    """(subject, body). Counts, money, order numbers and product names only."""
    base = base_url.rstrip("/")
    waiting = d["waiting_to_order"] + d["failed_supplier"] + len(d["refunds"]) + d["exceptions"] + len(d["stuck"])
    subject = f"{d['business']}: {d['paid_24h']} paid order{'' if d['paid_24h'] == 1 else 's'}, {_money(d['revenue_24h'])} yesterday" + (f", {waiting} waiting on you" if waiting else "")
    out = [f"Good morning,\n\nHere is how {d['business']} did in the last 24 hours.\n",
           f"Orders placed: {d['orders_24h']}", f"Orders paid: {d['paid_24h']}", f"Revenue: {_money(d['revenue_24h'])} (last 7 days: {_money(d['revenue_7d'])})"]
    m = d.get("margin_7d")
    if m:
        today = d.get("margin_24h")
        out.append(f"Product margin, last 7 days: {_money(m['margin'])} on {_money(m['sales'])} of product sales ({m['percent']}%)"
                   + (f"; last 24 hours: {_money(today['margin'])}" if today else ""))
        out.append("  (an estimate from today's supplier costs, before shipping, tax and refunds"
                   + (f"; {m['uncosted_lines']} order line{'' if m['uncosted_lines'] == 1 else 's'} left out because the item has no supplier cost" if m["uncosted_lines"] else "") + ")")
    if waiting:
        out.append("\nWaiting on you")
        if d["waiting_to_order"]:
            out.append(f"- {d['waiting_to_order']} supplier order{'' if d['waiting_to_order'] == 1 else 's'} to place with the supplier: {base}/dropshipping/fulfilment")
        if d["failed_supplier"]:
            out.append(f"- {d['failed_supplier']} supplier order{'' if d['failed_supplier'] == 1 else 's'} failed and need another try: {base}/dropshipping/fulfilment")
        if d["refunds"]:
            out.append(f"- Refunds to make in Stripe: {', '.join(d['refunds'])} ({base}/dropshipping/orders)")
        if d["exceptions"]:
            out.append(f"- {d['exceptions']} parcel{'' if d['exceptions'] == 1 else 's'} with a delivery problem: {base}/dropshipping/orders")
        if d["stuck"]:
            out.append(f"- Paid more than 48 hours ago and not shipped yet: {', '.join(d['stuck'])} ({base}/dropshipping/orders)")
    if d["sold_out"]:
        shown = d["sold_out"][:MAX_NAMES]
        out.append(f"\nSold out ({len(d['sold_out'])})\n" + "\n".join(f"- {n}" for n in shown) + (f"\n...and {len(d['sold_out']) - len(shown)} more" if len(d["sold_out"]) > len(shown) else "") + f"\n{base}/dropshipping/catalog")
    if d["low"]:
        out.append(f"\nRunning low ({len(d['low'])})\n" + "\n".join(f"- {n}" for n in d["low"][:MAX_NAMES]))
    out.append("\nNo customer details are in this e-mail, and it was sent only to your team.")
    return subject, "\n".join(out)


async def send_store_digest(session_factory, tenant_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, Any]:
    """Send today's digest unless one already went today, the day was quiet, or no e-mail provider is set. Returns {'sent': int, 'reason': str | None}."""
    from app.communications.base import MessageTemplate
    from app.communications.email_config import email_configured
    from app.communications.factory import get_internal_channel

    data = await gather(session_factory, tenant_id, now)
    if data["already_sent_today"]:
        return {"sent": 0, "reason": "already_sent_today"}
    if not data["owners"]:
        return {"sent": 0, "reason": "no_owner_email"}
    if not email_configured():
        return {"sent": 0, "reason": "email_not_configured"}
    if is_quiet(data):
        return {"sent": 0, "reason": "quiet_day"}
    subject, body = compose(data, get_settings().FRONTEND_BASE_URL)
    channel = get_internal_channel(session_factory)
    sent = 0
    for to in data["owners"]:
        if await channel.send_email(tenant_id, to=to, subject=subject, body=body, template=MessageTemplate.OWNER_DIGEST):
            sent += 1
    if sent:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=None, action=SENT_ACTION, entity_type="organization", entity_id=tenant_id,
                                 input_summary={"orders_24h": data["orders_24h"], "paid_24h": data["paid_24h"], "recipients": sent}, result="success"))
            await session.commit()
    return {"sent": sent, "reason": None if sent else "not_delivered"}


async def run_store_digest_sweeper(session_factory, tenant_id_getter, stop: "asyncio.Event", every_seconds: float = 1800.0) -> None:
    """Background loop: from the configured hour (UTC) each day, sends the day's digest once. The once-a-day rule lives in `send_store_digest`, so a restart or a second check never doubles it."""
    import asyncio

    while not stop.is_set():
        try:
            tenant_id = tenant_id_getter()
            if tenant_id is not None and datetime.now(timezone.utc).hour >= get_settings().DROPSHIPPING_DIGEST_HOUR_UTC:
                await send_store_digest(session_factory, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("store_digest_failed", error_type=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_seconds)
        except asyncio.TimeoutError:
            pass
