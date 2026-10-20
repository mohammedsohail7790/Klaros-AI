"""Dropshipping store, branch 4: what the Stripe webhook does when a storefront order is paid (`metadata.purpose == "dropshipping_order"`).

Called from `app/api/v1/webhooks.py::_handle_payment_intent_succeeded`, next to the quote-deposit branch. Returns `(tenant_id, error_detail)` like the other handlers.
Error prefixes matter: "record_payment failed:" and "mark_paid failed:" are transient and make the webhook answer 502 so Stripe redelivers (both steps are idempotent);
anything else is permanent, is recorded on the webhook row, and is a 200.

The rules:
- the order must belong to the tenant in the metadata;
- the Payment (the core money record) is written whenever Stripe really took money, even when the order cannot be advanced: it is the truth about the money;
- the amount must equal the order total; a mismatch records the payment, leaves the order unpaid, and flags it for staff;
- a payment that arrives for an order already cancelled (or refund-requested) is recorded and flagged "refund needed", never silently dropped;
- otherwise the order moves to PAID through the branch 3 `mark_paid`, which creates the supplier work queue. A redelivery changes nothing.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.models.dropshipping import Order, OrderStatus
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, _audit
from app.services.dropshipping_order_rules import OrderTransitionError
from app.services.dropshipping_order_service import DropshippingOrderService
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.payment_service import PaymentService


async def _flag(tenant_id: uuid.UUID, order_id: uuid.UUID, note: str, *, reference: str | None, action: str) -> None:
    """Puts a visible note on the order (and the payment reference, if it has none) and writes an audit row. The order's status is not changed."""
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        order = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.id == order_id))).scalar_one_or_none()
        if order is None:
            return
        if note not in (order.status_note or ""):
            order.status_note = f"{order.status_note}\n{note}" if order.status_note else note
        if reference and order.payment_reference is None:
            order.payment_reference, order.payment_source = reference, "STRIPE"
        _audit(session, tenant_id, None, action, "dropshipping_order", order.id, {"order_number": order.order_number, "reference": reference})
        await session.commit()


async def handle_store_payment_succeeded(
    tenant_id: uuid.UUID, *, customer_id: uuid.UUID, amount: Decimal, currency: str | None, payment_intent_id: str, order_id_raw: str | None,
    bus: EventBus, connection_service: IntegrationConnectionService,
) -> tuple[uuid.UUID | None, str | None]:
    if not order_id_raw:
        return tenant_id, "missing order_id metadata on dropshipping-order PaymentIntent"
    try:
        order_id = uuid.UUID(order_id_raw)
    except ValueError as exc:
        return tenant_id, f"malformed order_id in PaymentIntent metadata: {exc}"

    # The order must really belong to the claimed tenant BEFORE any Payment is written (the same guard the quote-deposit branch uses).
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        order = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.id == order_id))).scalar_one_or_none()
        if order is None:
            return tenant_id, f"order {order_id} does not belong to tenant {tenant_id}"
        total, order_currency, status, number = Decimal(order.total).quantize(Decimal("0.01")), order.currency, order.status, order.order_number

    payment_service = PaymentService(async_session_maker, bus, connection_service)
    try:
        await payment_service.record_payment(
            tenant_id, customer_id=customer_id, amount=amount, provider="stripe", external_id=payment_intent_id, payment_method="card", allocations=[],
        )
    except Exception as exc:  # noqa: BLE001 - reported as a processing failure; transient, so Stripe redelivers (record_payment is idempotent)
        return tenant_id, f"record_payment failed: {exc}"

    if amount.quantize(Decimal("0.01")) != total or (currency and currency.upper() != order_currency.upper()):
        await _flag(tenant_id, order_id, f"Payment of {amount} {currency or ''} received but the order total is {total} {order_currency}: review and refund or correct by hand.".replace("  ", " "),
                    reference=payment_intent_id, action="dropshipping.order.payment_mismatch")
        return tenant_id, f"dropshipping_order rejected: amount received {amount} does not match order {number} total {total}"

    if status in (OrderStatus.CANCELLED.value, OrderStatus.REFUND_REQUESTED.value):
        await _flag(tenant_id, order_id, f"Paid after the order was {status.lower().replace('_', ' ')}: refund needed.", reference=payment_intent_id, action="dropshipping.order.late_payment")
        return tenant_id, f"dropshipping_order rejected: payment arrived for order {number} which is {status}; refund needed"

    try:
        await DropshippingOrderService(async_session_maker).mark_paid(tenant_id, order_id, payment_reference=payment_intent_id, source="STRIPE")
    except (OrderTransitionError, CatalogConflictError, CatalogValidationError) as exc:
        # a permanent state mismatch (e.g. the order was already paid with a different reference): the payment is recorded, staff are told
        await _flag(tenant_id, order_id, f"Stripe payment {payment_intent_id} could not be applied: {exc}", reference=None, action="dropshipping.order.payment_not_applied")
        return tenant_id, f"mark_paid rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 - transient; mark_paid is idempotent on the reference, so a redelivery completes it
        return tenant_id, f"mark_paid failed: {exc}"
    return tenant_id, None
