"""Dropshipping store, branch 4: the shopper's checkout. Creates the order (branch 3) and a hosted Stripe Checkout Session for it.

Mirrors `QuoteDepositService`: the shopper is redirected to Stripe's own page and confirmation always comes from the webhook (`dropshipping_store_payment_service`),
never from the redirect. The shopper can send items, contact details, the ship-to address and an idempotency key, and nothing else: prices come from the catalog, shipping
from the owner's rule, and both redirect URLs are built here from FRONTEND_BASE_URL (never accepted from the client, so there is no open redirect).
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.integrations.stripe_client import StripeAPIError, StripeClient
from app.models.dropshipping import Order, OrderStatus
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogNotFoundError
from app.services.dropshipping_order_rules import normalize_order_lines
from app.services.dropshipping_order_service import DropshippingOrderService
from app.services.dropshipping_store_service import DropshippingStoreService, compute_shipping
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.quote_deposit_service import StripeNotConfiguredError
from app.tools.builtin.stripe_tools import resolve_stripe_secret_key

PURPOSE = "dropshipping_order"
_KEY_PREFIX = "web-"  # keeps shopper keys apart from staff-entered orders in the one idempotency space


class StoreClosedError(Exception):
    pass


class TooManyUnpaidOrdersError(Exception):
    pass


class OrderNotPayableError(Exception):
    pass


class StoreCheckoutError(Exception):
    pass


class DropshippingCheckoutService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service
        self._orders = DropshippingOrderService(session_factory)
        self._store = DropshippingStoreService(session_factory, connection_service)

    async def checkout(self, tenant_id: uuid.UUID, *, lines: object, contact: object, ship_to: object, idempotency_key: object) -> dict:
        key = str(idempotency_key or "").strip()
        if not 8 <= len(key) <= 100:
            raise CatalogValidationError(["idempotency_key is required (8-100 characters)"])
        store_key = _KEY_PREFIX + key
        is_open, store_settings = await self._store.is_open(tenant_id)
        if not is_open:
            raise StoreClosedError("The store is not open")
        secret_key = await resolve_stripe_secret_key(self._connection_service, tenant_id)
        if not secret_key:
            raise StripeNotConfiguredError("Stripe is not connected")

        existing = await self._order_by_key(tenant_id, store_key)
        if existing is not None:
            shipping = existing.shipping  # a retry keeps the price it was first given, even if the owner has changed the rule since
        else:
            await self._check_unpaid_cap(tenant_id)
            refs = normalize_order_lines(lines)
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                skus = await DropshippingOrderService._resolve_skus(session, tenant_id, refs, strict=True)
            subtotal = sum((Decimal(s.price) * q for s, q in skus), Decimal("0.00"))
            shipping = compute_shipping(store_settings, subtotal)

        order, _created = await self._orders.create_order(tenant_id, lines=lines, contact=contact, ship_to=ship_to, idempotency_key=store_key, shipping=str(shipping))
        row = await self._load(tenant_id, uuid.UUID(order["id"]))
        return await self._start_session(tenant_id, row, secret_key, attempt="first")

    async def pay_again(self, tenant_id: uuid.UUID, public_token: str) -> dict:
        """A fresh Checkout Session for an order still awaiting payment (the shopper closed the Stripe page, or the card was declined)."""
        is_open, _ = await self._store.is_open(tenant_id)
        if not is_open:
            raise StoreClosedError("The store is not open")
        secret_key = await resolve_stripe_secret_key(self._connection_service, tenant_id)
        if not secret_key:
            raise StripeNotConfiguredError("Stripe is not connected")
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.public_token == public_token))).scalar_one_or_none()
        if row is None or row.personal_data_erased_at is not None:
            raise CatalogNotFoundError("Order not found")
        if row.status != OrderStatus.PENDING_PAYMENT.value:
            raise OrderNotPayableError("This order is not waiting for payment.")
        # an hourly bucket: a double-click gets the same Stripe session back, a shopper returning later gets a new one
        return await self._start_session(tenant_id, row, secret_key, attempt=f"r{int(time.time() // 3600)}")

    # --- internals ------------------------------------------------------------------------------------------------------------------------------------

    async def _order_by_key(self, tenant_id: uuid.UUID, key: str) -> Order | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.idempotency_key == key))).scalar_one_or_none()

    async def _load(self, tenant_id: uuid.UUID, order_id: uuid.UUID) -> Order:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.id == order_id))).scalar_one_or_none()
        if row is None:
            raise CatalogNotFoundError("Order not found")
        return row

    async def _check_unpaid_cap(self, tenant_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            unpaid = (await session.execute(select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id, Order.status == OrderStatus.PENDING_PAYMENT.value))).scalar_one()
        if unpaid >= get_settings().DROPSHIPPING_MAX_UNPAID_ORDERS:
            raise TooManyUnpaidOrdersError("Too many unpaid orders")

    async def _start_session(self, tenant_id: uuid.UUID, order: Order, secret_key: str, *, attempt: str) -> dict:
        result = {"order_number": order.order_number, "status_token": order.public_token, "status": order.status, "total": str(Decimal(order.total).quantize(Decimal("0.01"))), "checkout_url": None}
        if order.status != OrderStatus.PENDING_PAYMENT.value:
            return result  # a replayed checkout of an order that is already paid: nothing to pay
        base = get_settings().FRONTEND_BASE_URL.rstrip("/")
        status_url = f"{base}/store/orders/{order.public_token}"
        total = Decimal(order.total).quantize(Decimal("0.01"))
        try:
            session_obj = await StripeClient(secret_key).create_checkout_session(
                amount=total, currency=order.currency,
                metadata={"tenant_id": str(tenant_id), "order_id": str(order.id), "customer_id": str(order.customer_id), "purpose": PURPOSE},
                success_url=f"{status_url}?payment=success", cancel_url=f"{status_url}?payment=cancelled",
                description=f"Order {order.order_number}", customer_email=order.contact_email or None,
                idempotency_key=f"klaros-ds-order-{order.id}-{total}-{attempt}",
            )
        except StripeAPIError as exc:
            raise StoreCheckoutError(f"Stripe checkout session creation failed: {exc}") from exc
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            fresh = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.id == order.id))).scalar_one()
            fresh.stripe_session_id = session_obj.id
            await session.commit()
        result["checkout_url"] = session_obj.url
        return result
