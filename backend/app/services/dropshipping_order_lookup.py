"""Dropshipping: "where is my order?" for a person who is not signed in (a caller on the phone, a chat visitor), answered by an AI helper or by Halla.

Safe by construction:
  - an order is only revealed to someone who knows its number AND the e-mail address or phone number it was placed with (both, when both are given). Anything else, whether the
    number does not exist, belongs to someone else, was erased or the proof is wrong, gets the SAME answer, so the lookup cannot be used to find out which orders exist;
  - repeated wrong guesses are limited (per order number and per store, per hour) and also get that same answer;
  - the answer carries no address, e-mail, phone, name, payment reference, supplier or internal note: the order number, a plain status line, the item names and quantities, the total
    and the tracking facts a customer is entitled to (carrier, tracking number and link);
  - the proof itself is never written to the audit log (the tool is flagged to have its input masked) and failed attempts record only the order number.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.dropshipping import Order, OrderLine, OrderStatus, Shipment, SupplierOrder
from app.services.dropshipping_store_service import STATUS_LABELS

FAILED_ACTION = "dropshipping.order_lookup_failed"
FAILED_ENTITY = "dropshipping_order_lookup"
WINDOW = timedelta(hours=1)
MAX_FAILS_PER_ORDER = 5
MAX_FAILS_PER_STORE = 100
NOT_FOUND = {"found": False, "message": "We could not find an order matching those details."}
_NUMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,38}$")
_NAMESPACE = uuid.UUID("5d0b6f7a-3d8c-4b0e-9a55-0c7a1a1d0c01")


def _number(value: object) -> str | None:
    text = str(value or "").strip().upper().replace(" ", "")
    return text if _NUMBER_RE.match(text) else None


def _email(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if "@" in text and 3 <= len(text) <= 254 and " " not in text else None


def _phone(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def _same_failure_key(tenant_id: uuid.UUID, number: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{tenant_id}:{number}")


def status_sentence(view: dict) -> str:
    """One plain sentence a voice agent can read out."""
    s, n = view["status"], view["order_number"]
    if s == OrderStatus.SHIPPED.value or s == OrderStatus.DELIVERED.value:
        parcels = [f"{t['carrier'] or 'the carrier'}, tracking number {t['tracking_number']}" for t in view["tracking"] if t.get("tracking_number")]
        tail = (" It is on its way with " + " and ".join(parcels) + ".") if parcels and s == OrderStatus.SHIPPED.value else ""
        return f"Order {n} is {STATUS_LABELS.get(s, s).lower()}.{tail}"
    return f"Order {n}: {STATUS_LABELS.get(s, s)}"


class DropshippingOrderLookupService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def lookup(self, tenant_id: uuid.UUID, *, order_number: object, email: object = None, phone: object = None, actor_id: uuid.UUID | None = None) -> dict:
        number, mail, tel = _number(order_number), _email(email), _phone(phone)
        if number is None or (mail is None and tel is None) or (email and mail is None) or (phone and tel is None):
            return dict(NOT_FOUND)  # nothing to check against, or a malformed value: no database work, no information
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            since = datetime.now(timezone.utc) - WINDOW
            key = _same_failure_key(tenant_id, number)
            per_order = (await session.execute(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == FAILED_ACTION, AuditLog.entity_id == key, AuditLog.created_at >= since))).scalar_one()
            per_store = (await session.execute(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == FAILED_ACTION, AuditLog.created_at >= since))).scalar_one()
            if per_order >= MAX_FAILS_PER_ORDER or per_store >= MAX_FAILS_PER_STORE:
                return dict(NOT_FOUND)
            order = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, func.upper(Order.order_number) == number))).scalar_one_or_none()
            proven = (
                order is not None and order.personal_data_erased_at is None
                and (mail is None or _email(order.contact_email) == mail)
                and (tel is None or _phone(order.contact_phone) == tel)
            )
            if not proven:
                session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=actor_id, action=FAILED_ACTION, entity_type=FAILED_ENTITY, entity_id=key,
                                     input_summary={"order_number": number}, result="failure"))
                await session.commit()
                return dict(NOT_FOUND)
            lines = list((await session.execute(select(OrderLine).where(OrderLine.tenant_id == tenant_id, OrderLine.order_id == order.id))).scalars())
            sos = list((await session.execute(select(SupplierOrder).where(SupplierOrder.tenant_id == tenant_id, SupplierOrder.order_id == order.id))).scalars())
            shipments = list((await session.execute(select(Shipment).where(Shipment.tenant_id == tenant_id, Shipment.supplier_order_id.in_([s.id for s in sos])))).scalars()) if sos else []
            view = {
                "order_number": order.order_number, "status": order.status, "status_label": STATUS_LABELS.get(order.status, order.status),
                "placed_on": order.placed_at.date().isoformat() if order.placed_at else None, "currency": order.currency,
                "total": str(Decimal(order.total).quantize(Decimal("0.01"))),
                "items": [{"name": ln.name_snapshot, "quantity": ln.quantity} for ln in lines],
                "tracking": [{"carrier": sh.carrier, "tracking_number": sh.tracking_number, "tracking_url": sh.tracking_url, "status": sh.status} for sh in shipments],
            }
        view["summary"] = status_sentence(view)
        return {"found": True, "order": view}
