"""Dropshipping store, branch 3: the order service (create, record payment, supplier orders, tracking, delivery, cancel, erase).

Domain logic lives here, never in routes or tools. The state machine is in `dropshipping_order_rules.py`; every status change goes through `_move`, so no route
sets a status directly, and each change writes an `AuditLog` row in the same transaction as the change. Klaros never moves money here: recording a payment stores a
reference, and cancelling a paid order only raises a REFUND_REQUESTED flag for staff to act on in Stripe.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.crm import Customer, CustomerStatus
from app.models.dropshipping import (
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
    Supplier,
    SupplierOrder,
    SupplierOrderStatus,
)
from app.services.customer_matching import find_matching_customer, normalize_email, normalize_phone
from app.services.dropshipping_catalog_import import CURRENCY, CatalogValidationError, parse_money
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError, _audit
from app.services.dropshipping_order_emails import DELIVERED, PAID, SHIPPED, notify_order
from app.services.dropshipping_order_rules import (
    ERASABLE,
    OrderTransitionError,
    check_transition,
    new_order_number,
    new_public_token,
    normalize_contact,
    normalize_order_lines,
    normalize_reference,
    normalize_ship_to,
    normalize_tracking,
)

PAYMENT_SOURCES = ("STRIPE", "MANUAL")
_PLACED = (SupplierOrderStatus.PLACED.value, SupplierOrderStatus.CONFIRMED.value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def order_to_dict(order: Order, lines: list[OrderLine], supplier_orders: list[SupplierOrder], shipments: list[Shipment], suppliers: dict[uuid.UUID, Supplier],
                  skus: dict[uuid.UUID, Sku], *, include_personal: bool = True) -> dict:
    """`include_personal=False` is the shape for a tool or any future public view: no address, email or phone."""
    shipments_by_so: dict[uuid.UUID, list[Shipment]] = {}
    for sh in shipments:
        shipments_by_so.setdefault(sh.supplier_order_id, []).append(sh)
    out = {
        "id": str(order.id), "order_number": order.order_number, "status": order.status, "currency": order.currency,
        "subtotal": str(_money(order.subtotal)), "shipping": str(_money(order.shipping)), "tax": str(_money(order.tax)), "total": str(_money(order.total)),
        "placed_at": order.placed_at.isoformat() if order.placed_at else None, "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None, "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "payment_source": order.payment_source, "status_note": order.status_note, "personal_data_erased": order.personal_data_erased_at is not None,
        "lines": [{"sku_id": str(ln.sku_id), "sku_code": skus[ln.sku_id].sku_code if ln.sku_id in skus else None, "name": ln.name_snapshot,
                   "unit_price": str(_money(ln.unit_price)), "quantity": ln.quantity, "line_total": str(_money(ln.line_total))} for ln in lines],
        "supplier_orders": [{
            "id": str(so.id), "supplier": suppliers[so.supplier_id].name if so.supplier_id in suppliers else None, "status": so.status,
            "supplier_reference": so.supplier_reference, "note": so.note,
            "shipments": [{"id": str(sh.id), "carrier": sh.carrier, "tracking_number": sh.tracking_number, "tracking_url": sh.tracking_url, "status": sh.status,
                           "note": sh.note, "shipped_at": sh.shipped_at.isoformat() if sh.shipped_at else None,
                           "delivered_at": sh.delivered_at.isoformat() if sh.delivered_at else None} for sh in shipments_by_so.get(so.id, [])],
        } for so in supplier_orders],
    }
    if include_personal:
        out.update({"contact_email": order.contact_email, "contact_phone": order.contact_phone, "ship_to": order.ship_to, "payment_reference": order.payment_reference,
                    "customer_id": str(order.customer_id) if order.customer_id else None})
    return out


class DropshippingOrderService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # --- reading -----------------------------------------------------------------------------------------------------------------------------------

    async def _load(self, session, tenant_id: uuid.UUID, orders: list[Order], *, include_personal: bool) -> list[dict]:
        if not orders:
            return []
        ids = [o.id for o in orders]
        lines = list((await session.execute(select(OrderLine).where(OrderLine.tenant_id == tenant_id, OrderLine.order_id.in_(ids)))).scalars())
        sos = list((await session.execute(select(SupplierOrder).where(SupplierOrder.tenant_id == tenant_id, SupplierOrder.order_id.in_(ids)))).scalars())
        shipments = list((await session.execute(select(Shipment).where(Shipment.tenant_id == tenant_id, Shipment.supplier_order_id.in_([s.id for s in sos])))).scalars()) if sos else []
        suppliers = {s.id: s for s in (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id))).scalars()}
        skus = {k.id: k for k in (await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.id.in_({ln.sku_id for ln in lines})))).scalars()} if lines else {}
        return [
            order_to_dict(o, [ln for ln in lines if ln.order_id == o.id], [so for so in sos if so.order_id == o.id], shipments, suppliers, skus, include_personal=include_personal)
            for o in orders
        ]

    async def get_order(self, tenant_id: uuid.UUID, order_id: uuid.UUID, *, include_personal: bool = True) -> dict:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = await self._order(session, tenant_id, order_id)
            return (await self._load(session, tenant_id, [order], include_personal=include_personal))[0]

    async def search_orders(
        self, tenant_id: uuid.UUID, *, status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0, include_personal: bool = True
    ) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(Order).where(Order.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(Order.status == status)
            if q and q.strip():
                stmt = stmt.where(func.upper(Order.order_number).like(f"%{q.strip().upper()}%"))
            orders = list((await session.execute(stmt.order_by(Order.placed_at.desc()).limit(limit).offset(max(0, int(offset))))).scalars())
            return await self._load(session, tenant_id, orders, include_personal=include_personal)

    async def list_supplier_orders(self, tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        """The work queue: per supplier order, exactly what to order and where it ships (staff need the full address)."""
        limit = max(1, min(int(limit), 200))
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(SupplierOrder).where(SupplierOrder.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(SupplierOrder.status == status)
            sos = list((await session.execute(stmt.order_by(SupplierOrder.created_at).limit(limit).offset(max(0, int(offset))))).scalars())
            if not sos:
                return []
            orders = {o.id: o for o in (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.id.in_({s.order_id for s in sos})))).scalars()}
            suppliers = {s.id: s for s in (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id))).scalars()}
            lines = list((await session.execute(select(OrderLine).where(OrderLine.tenant_id == tenant_id, OrderLine.order_id.in_(list(orders))))).scalars())
            skus = {k.id: k for k in (await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.id.in_({ln.sku_id for ln in lines})))).scalars()}
            shipments = list((await session.execute(select(Shipment).where(Shipment.tenant_id == tenant_id, Shipment.supplier_order_id.in_([s.id for s in sos])))).scalars())
            out = []
            for so in sos:
                order = orders[so.order_id]
                items = [{"supplier_sku": skus[ln.sku_id].supplier_sku, "sku_code": skus[ln.sku_id].sku_code, "name": ln.name_snapshot, "quantity": ln.quantity}
                         for ln in lines if ln.order_id == so.order_id and skus[ln.sku_id].supplier_id == so.supplier_id]
                out.append({
                    "id": str(so.id), "order_id": str(order.id), "order_number": order.order_number, "order_status": order.status, "status": so.status,
                    "supplier": suppliers[so.supplier_id].name if so.supplier_id in suppliers else None, "supplier_reference": so.supplier_reference, "note": so.note,
                    "items": items, "ship_to": order.ship_to, "contact_name": (order.ship_to or {}).get("name"),
                    "shipments": [{"id": str(s.id), "carrier": s.carrier, "tracking_number": s.tracking_number, "status": s.status} for s in shipments if s.supplier_order_id == so.id],
                })
            return out

    @staticmethod
    async def _order(session, tenant_id: uuid.UUID, order_id: uuid.UUID) -> Order:
        order = (await session.execute(select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id))).scalar_one_or_none()
        if order is None:
            raise CatalogNotFoundError(f"Order {order_id} not found")
        return order

    # --- creating an order -------------------------------------------------------------------------------------------------------------------------

    async def create_order(
        self, tenant_id: uuid.UUID, *, lines: object, contact: object, ship_to: object, idempotency_key: str, shipping: object = "0", actor_id: uuid.UUID | None = None
    ) -> tuple[dict, bool]:
        """Returns (order, created). The same idempotency key with the same content returns the existing order; with different content it is a conflict."""
        key = str(idempotency_key or "").strip()
        problems: list[str] = []
        if not 8 <= len(key) <= 120:
            problems.append("idempotency_key is required (8-120 characters)")
        parsed_lines = ship = who = fee = None
        for label, fn, value in (("lines", normalize_order_lines, lines), ("contact", normalize_contact, contact), ("ship_to", normalize_ship_to, ship_to)):
            try:
                result = fn(value)
            except CatalogValidationError as exc:
                problems.extend(exc.problems)
                continue
            if label == "lines":
                parsed_lines = result
            elif label == "contact":
                who = result
            else:
                ship = result
        try:
            fee = parse_money(shipping if shipping not in (None, "") else "0", "shipping", allow_zero=True)
        except ValueError as exc:
            problems.append(str(exc))
        if problems:
            raise CatalogValidationError(problems)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = await self._by_key(session, tenant_id, key)
            # A replay of an earlier request is judged on what was ordered, not on today's stock: loose resolution for it, strict for a new order.
            skus = await self._resolve_skus(session, tenant_id, parsed_lines, strict=existing is None)  # strict raises CatalogValidationError with every line's problem
            requested = {(s.id, q) for s, q in skus}
            if existing is not None:
                return await self._same_or_conflict(session, tenant_id, existing, requested, ship, who, fee), False

            products = {p.id: p for p in (await session.execute(select(Product).where(Product.tenant_id == tenant_id, Product.id.in_({s.product_id for s, _ in skus})))).scalars()}
            line_rows, subtotal = [], Decimal("0.00")
            for sku, qty in skus:
                total = _money(Decimal(sku.price) * qty)
                subtotal += total
                line_rows.append((sku, qty, total, products[sku.product_id].name))
            customer = await find_matching_customer(session, tenant_id=tenant_id, email=who["email"], phone=who["phone"])
            if customer is None:
                customer = Customer(
                    tenant_id=tenant_id, name=who["name"], email=normalize_email(who["email"]), phone=who["phone"], phone_normalized=normalize_phone(who["phone"]),
                    address=ship["line1"], city=ship["city"], state=ship["state"], postal_code=ship["postal_code"], status=CustomerStatus.PROSPECT.value,
                )
                session.add(customer)
                await session.flush()
            number = new_order_number()
            while (await session.execute(select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id, Order.order_number == number))).scalar_one():
                number = new_order_number()  # a collision is vanishingly rare; the unique constraint is the backstop for a race
            order = Order(
                tenant_id=tenant_id, customer_id=customer.id, order_number=number, public_token=new_public_token(), status=OrderStatus.PENDING_PAYMENT.value,
                contact_email=who["email"], contact_phone=who["phone"], ship_to=ship, currency=CURRENCY, subtotal=subtotal, shipping=fee,
                tax=Decimal("0.00"), total=_money(subtotal + fee), idempotency_key=key, placed_at=_now(),
            )
            session.add(order)
            await session.flush()
            for sku, qty, total, name in line_rows:
                session.add(OrderLine(tenant_id=tenant_id, order_id=order.id, sku_id=sku.id, name_snapshot=name, unit_price=sku.price, quantity=qty, line_total=total))
            _audit(session, tenant_id, actor_id, "dropshipping.order.created", "dropshipping_order", order.id, {"order_number": order.order_number, "lines": len(line_rows), "total": str(order.total)})
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                await set_tenant_context(session, tenant_id)
                existing = await self._by_key(session, tenant_id, key)  # a concurrent identical request won the race
                if existing is None:
                    raise
                return await self._same_or_conflict(session, tenant_id, existing, requested, ship, who, fee), False
            return (await self._load(session, tenant_id, [order], include_personal=True))[0], True

    @staticmethod
    async def _by_key(session, tenant_id: uuid.UUID, key: str) -> Order | None:
        return (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.idempotency_key == key))).scalar_one_or_none()

    async def _same_or_conflict(self, session, tenant_id, existing: Order, requested: set, ship: dict, who: dict, fee: Decimal) -> dict:
        have = {(ln.sku_id, ln.quantity) for ln in (await session.execute(select(OrderLine).where(OrderLine.order_id == existing.id))).scalars()}
        if have != requested or existing.ship_to != ship or (existing.contact_email or None) != who["email"] or _money(existing.shipping) != fee:
            raise CatalogConflictError("That idempotency key was already used for a different order.")
        return (await self._load(session, tenant_id, [existing], include_personal=True))[0]

    @staticmethod
    async def _resolve_skus(session, tenant_id: uuid.UUID, refs: list[tuple[str, int]], *, strict: bool = True) -> list[tuple[Sku, int]]:
        by_id, by_code = [], []
        for ref, _ in refs:
            try:
                by_id.append(uuid.UUID(ref))
            except ValueError:
                by_code.append(ref)
        found: dict[str, Sku] = {}
        stmt = select(Sku).where(Sku.tenant_id == tenant_id, or_(Sku.id.in_(by_id) if by_id else False, Sku.sku_code.in_(by_code) if by_code else False))
        for sku in (await session.execute(stmt)).scalars():
            found[str(sku.id)] = sku
            found[sku.sku_code] = sku
        products = {p.id: p for p in (await session.execute(select(Product).where(Product.tenant_id == tenant_id, Product.id.in_({s.product_id for s in found.values()})))).scalars()} if found else {}
        problems, resolved = [], []
        for ref, qty in refs:
            sku = found.get(ref)
            if sku is None:
                problems.append(f"{ref}: unknown item")
            elif not strict:
                resolved.append((sku, qty))
            elif sku.status != SkuStatus.ACTIVE or products[sku.product_id].status != ProductStatus.ACTIVE:
                problems.append(f"{sku.sku_code}: this item is not for sale")
            elif sku.availability != SkuAvailability.AVAILABLE:
                problems.append(f"{sku.sku_code}: this item is not available right now")
            elif sku.currency != CURRENCY:
                problems.append(f"{sku.sku_code}: only {CURRENCY} items can be ordered")
            else:
                resolved.append((sku, qty))
        if problems:
            raise CatalogValidationError(problems)
        merged: dict[uuid.UUID, int] = {}
        for sku, qty in resolved:
            merged[sku.id] = merged.get(sku.id, 0) + qty
        by_sku = {s.id: s for s, _ in resolved}
        return [(by_sku[i], q) for i, q in merged.items()]

    # --- status changes (the single path) ------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _move(session, tenant_id: uuid.UUID, order: Order, target: OrderStatus, actor_id: uuid.UUID | None, action: str, extra: dict | None = None) -> None:
        check_transition(order.status, target.value)
        before = order.status
        order.status = target.value
        _audit(session, tenant_id, actor_id, action, "dropshipping_order", order.id, {"order_number": order.order_number, "from": before, "to": target.value, **(extra or {})})

    # --- payment ------------------------------------------------------------------------------------------------------------------------------------

    async def mark_paid(self, tenant_id: uuid.UUID, order_id: uuid.UUID, *, payment_reference: object, source: str, actor_id: uuid.UUID | None = None) -> dict:
        """The one entry point for a payment: branch 4's Stripe handler calls it, and staff can for a payment received another way (a reference is mandatory).
        Idempotent on the reference. Creates one supplier order per supplier among the lines: that is the work queue."""
        reference = normalize_reference(payment_reference)
        if source not in PAYMENT_SOURCES:
            raise CatalogValidationError(["source must be STRIPE or MANUAL"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = await self._order(session, tenant_id, order_id)
            if order.payment_reference is not None:
                if order.payment_reference == reference:
                    return (await self._load(session, tenant_id, [order], include_personal=True))[0]
                raise CatalogConflictError("This order was already paid with a different reference.")
            self._move(session, tenant_id, order, OrderStatus.PAID, actor_id, "dropshipping.order.paid", {"source": source})
            order.paid_at, order.payment_reference, order.payment_source = _now(), reference, source
            lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars())
            skus = {k.id: k for k in (await session.execute(select(Sku).where(Sku.id.in_({ln.sku_id for ln in lines})))).scalars()}
            for supplier_id in sorted({skus[ln.sku_id].supplier_id for ln in lines}, key=str):
                session.add(SupplierOrder(tenant_id=tenant_id, order_id=order.id, supplier_id=supplier_id, status=SupplierOrderStatus.TO_PLACE.value))
            await session.commit()
            view = (await self._load(session, tenant_id, [order], include_personal=True))[0]
            await notify_order(PAID, order, view)  # only on the payment that was actually recorded: a repeat returned above
            return view

    # --- supplier orders -----------------------------------------------------------------------------------------------------------------------------

    async def mark_supplier_order(
        self, tenant_id: uuid.UUID, supplier_order_id: uuid.UUID, action: str, *, supplier_reference: str | None = None, note: str | None = None, actor_id: uuid.UUID | None = None
    ) -> dict:
        if action not in ("placed", "confirmed", "failed"):
            raise CatalogValidationError(["action must be placed, confirmed or failed"])
        note = (note or "").strip() or None
        if note and len(note) > 2000:
            raise CatalogValidationError(["note is longer than 2000 characters"])
        ref = (supplier_reference or "").strip() or None
        if action == "placed" and (ref is None or len(ref) > 160):
            raise CatalogValidationError(["supplier_reference (the supplier's own order number) is required, at most 160 characters"])
        if action == "failed" and note is None:
            raise CatalogValidationError(["a note saying what went wrong is required"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            so = await self._supplier_order(session, tenant_id, supplier_order_id)
            order = await self._order(session, tenant_id, so.order_id)
            if order.status not in (OrderStatus.PAID, OrderStatus.SUPPLIER_ORDER_PLACED):
                raise OrderTransitionError(f"A supplier order cannot be changed while the order is {order.status}.")
            allowed = {"placed": (SupplierOrderStatus.TO_PLACE, SupplierOrderStatus.FAILED), "confirmed": (SupplierOrderStatus.PLACED,),
                       "failed": (SupplierOrderStatus.TO_PLACE, SupplierOrderStatus.PLACED)}[action]
            if so.status not in {a.value for a in allowed}:
                raise OrderTransitionError(f"A supplier order that is {so.status} cannot be marked {action}.")
            so.status = {"placed": SupplierOrderStatus.PLACED, "confirmed": SupplierOrderStatus.CONFIRMED, "failed": SupplierOrderStatus.FAILED}[action].value
            if action == "placed":
                so.supplier_reference, so.placed_by, so.placed_at = ref, actor_id, _now()
            if note:
                so.note = note
            _audit(session, tenant_id, actor_id, f"dropshipping.supplier_order.{action}", "dropshipping_supplier_order", so.id, {"order_number": order.order_number, "supplier_reference": so.supplier_reference})
            await session.flush()
            siblings = list((await session.execute(select(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalars())
            if order.status == OrderStatus.PAID and all(s.status in _PLACED for s in siblings):
                self._move(session, tenant_id, order, OrderStatus.SUPPLIER_ORDER_PLACED, actor_id, "dropshipping.order.supplier_orders_placed")
            await session.commit()
            return (await self._load(session, tenant_id, [order], include_personal=True))[0]

    @staticmethod
    async def _supplier_order(session, tenant_id: uuid.UUID, supplier_order_id: uuid.UUID) -> SupplierOrder:
        so = (await session.execute(select(SupplierOrder).where(SupplierOrder.id == supplier_order_id, SupplierOrder.tenant_id == tenant_id))).scalar_one_or_none()
        if so is None:
            raise CatalogNotFoundError(f"Supplier order {supplier_order_id} not found")
        return so

    # --- tracking and delivery -----------------------------------------------------------------------------------------------------------------------

    async def add_shipment(
        self, tenant_id: uuid.UUID, supplier_order_id: uuid.UUID, *, carrier: object = None, tracking_number: object = None, tracking_url: object = None, actor_id: uuid.UUID | None = None
    ) -> dict:
        carrier_s, number, url = normalize_tracking(carrier, tracking_number, tracking_url)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            so = await self._supplier_order(session, tenant_id, supplier_order_id)
            order = await self._order(session, tenant_id, so.order_id)
            if so.status not in _PLACED or order.status not in (OrderStatus.PAID, OrderStatus.SUPPLIER_ORDER_PLACED, OrderStatus.SHIPPED):
                raise OrderTransitionError("Tracking can only be added once the supplier order has been placed (a second parcel of a shipped order is fine).")
            session.add(Shipment(tenant_id=tenant_id, supplier_order_id=so.id, carrier=carrier_s, tracking_number=number, tracking_url=url,
                                 status=ShipmentStatus.SHIPPED.value, shipped_at=_now(), entered_by=actor_id))
            _audit(session, tenant_id, actor_id, "dropshipping.shipment.added", "dropshipping_supplier_order", so.id, {"order_number": order.order_number, "carrier": carrier_s})
            await session.flush()
            before = order.status
            await self._roll_up_shipped(session, tenant_id, order, actor_id)
            await session.commit()
            view = (await self._load(session, tenant_id, [order], include_personal=True))[0]
            if before != OrderStatus.SHIPPED and order.status == OrderStatus.SHIPPED:
                await notify_order(SHIPPED, order, view)  # once: when the last supplier order gets its tracking, not for a second parcel
            return view

    async def mark_shipment(self, tenant_id: uuid.UUID, shipment_id: uuid.UUID, status: str, *, note: str | None = None, actor_id: uuid.UUID | None = None) -> dict:
        if status not in ("delivered", "exception"):
            raise CatalogValidationError(["status must be delivered or exception"])
        note = (note or "").strip() or None
        if status == "exception" and note is None:
            raise CatalogValidationError(["a note saying what went wrong is required"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            sh = (await session.execute(select(Shipment).where(Shipment.id == shipment_id, Shipment.tenant_id == tenant_id))).scalar_one_or_none()
            if sh is None:
                raise CatalogNotFoundError(f"Shipment {shipment_id} not found")
            so = await self._supplier_order(session, tenant_id, sh.supplier_order_id)
            order = await self._order(session, tenant_id, so.order_id)
            if sh.status == ShipmentStatus.DELIVERED:
                raise OrderTransitionError("This shipment is already delivered.")
            if order.status not in (OrderStatus.PAID, OrderStatus.SUPPLIER_ORDER_PLACED, OrderStatus.SHIPPED):
                raise OrderTransitionError(f"A shipment cannot be changed while the order is {order.status}.")
            sh.status = ShipmentStatus.DELIVERED.value if status == "delivered" else ShipmentStatus.EXCEPTION.value
            if status == "delivered":
                sh.delivered_at = _now()
            if note:
                sh.note = note
            _audit(session, tenant_id, actor_id, f"dropshipping.shipment.{status}", "dropshipping_shipment", sh.id, {"order_number": order.order_number})
            await session.flush()
            became_delivered = False
            if status == "delivered" and order.status == OrderStatus.SHIPPED:
                sos = list((await session.execute(select(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalars())
                shipments = list((await session.execute(select(Shipment).where(Shipment.supplier_order_id.in_([s.id for s in sos])))).scalars())
                if shipments and all(s.status == ShipmentStatus.DELIVERED for s in shipments) and all(any(s.supplier_order_id == so_.id for s in shipments) for so_ in sos):
                    self._move(session, tenant_id, order, OrderStatus.DELIVERED, actor_id, "dropshipping.order.delivered")
                    order.delivered_at = _now()
                    became_delivered = True
            await session.commit()
            view = (await self._load(session, tenant_id, [order], include_personal=True))[0]
            if became_delivered:
                await notify_order(DELIVERED, order, view)
            return view

    async def _roll_up_shipped(self, session, tenant_id: uuid.UUID, order: Order, actor_id: uuid.UUID | None) -> None:
        sos = list((await session.execute(select(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalars())
        shipments = list((await session.execute(select(Shipment).where(Shipment.supplier_order_id.in_([s.id for s in sos])))).scalars())
        if order.status == OrderStatus.SUPPLIER_ORDER_PLACED and all(any(sh.supplier_order_id == so.id for sh in shipments) for so in sos):
            self._move(session, tenant_id, order, OrderStatus.SHIPPED, actor_id, "dropshipping.order.shipped")

    # --- cancelling, refunds, erasure -----------------------------------------------------------------------------------------------------------------

    async def cancel_order(self, tenant_id: uuid.UUID, order_id: uuid.UUID, *, reason: object, actor_id: uuid.UUID | None = None) -> dict:
        """PENDING_PAYMENT -> CANCELLED. PAID and nothing placed with a supplier yet -> REFUND_REQUESTED (Klaros moves no money: staff refund in Stripe)."""
        reason_s = str(reason or "").strip()
        if not reason_s or len(reason_s) > 1000:
            raise CatalogValidationError(["a reason is required (at most 1000 characters)"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = await self._order(session, tenant_id, order_id)
            sos = list((await session.execute(select(SupplierOrder).where(SupplierOrder.order_id == order.id))).scalars())
            if order.status == OrderStatus.PENDING_PAYMENT:
                self._move(session, tenant_id, order, OrderStatus.CANCELLED, actor_id, "dropshipping.order.cancelled", {"reason": reason_s})
                order.status_note = reason_s
            elif order.status == OrderStatus.PAID and not any(s.status in _PLACED for s in sos):
                self._move(session, tenant_id, order, OrderStatus.REFUND_REQUESTED, actor_id, "dropshipping.order.cancelled", {"reason": reason_s})
                order.status_note = f"Cancelled before any supplier order was placed: refund needed. {reason_s}"
                for so in sos:
                    so.status = SupplierOrderStatus.CANCELLED.value
            else:
                raise OrderTransitionError(f"An order that is {order.status} cannot be cancelled here: a supplier order is already placed or the order is finished. Use a refund request.")
            order.cancelled_at = _now()
            await session.commit()
            return (await self._load(session, tenant_id, [order], include_personal=True))[0]

    async def request_refund(self, tenant_id: uuid.UUID, order_id: uuid.UUID, *, reason: object, actor_id: uuid.UUID | None = None) -> dict:
        """A work flag for a paid order that needs money returned (damaged, lost, returned). Klaros does not refund: staff do it in Stripe."""
        reason_s = str(reason or "").strip()
        if not reason_s or len(reason_s) > 1000:
            raise CatalogValidationError(["a reason is required (at most 1000 characters)"])
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = await self._order(session, tenant_id, order_id)
            self._move(session, tenant_id, order, OrderStatus.REFUND_REQUESTED, actor_id, "dropshipping.order.refund_requested", {"reason": reason_s})
            order.status_note = f"Refund needed: {reason_s}"
            await session.commit()
            return (await self._load(session, tenant_id, [order], include_personal=True))[0]

    async def erase_personal_data(self, tenant_id: uuid.UUID, order_id: uuid.UUID, *, actor_id: uuid.UUID | None = None) -> dict:
        """Clears the order's own copies of email, phone and address and kills its public status link. Totals, lines and tracking facts stay. The shared core
        `customers` row is core's own erasure concern and is not touched here."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = await self._order(session, tenant_id, order_id)
            if order.personal_data_erased_at is not None:
                return (await self._load(session, tenant_id, [order], include_personal=True))[0]
            if order.status not in ERASABLE:
                raise OrderTransitionError(f"Personal data can be erased once an order is finished (delivered, cancelled or refund requested), not while it is {order.status}.")
            order.contact_email, order.contact_phone, order.ship_to = None, None, None
            order.public_token = new_public_token()  # the old status link stops working
            order.personal_data_erased_at = _now()
            _audit(session, tenant_id, actor_id, "dropshipping.order.personal_data_erased", "dropshipping_order", order.id, {"order_number": order.order_number})
            await session.commit()
            return (await self._load(session, tenant_id, [order], include_personal=True))[0]
