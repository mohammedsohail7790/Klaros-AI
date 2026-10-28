"""Dropshipping store, branch 4: the store's own settings, the "is the store open" gate, and the shopper-facing read models.

The storefront serves ONE business. Its settings live in a single `tenant_settings` row (key `dropshipping_store`). The store is open to shoppers only when the owner has
switched it on AND the things that make selling safe are in place: a shipping rule, an explicit acknowledgement that sales tax is not collected, a payment route (Stripe),
and at least one product on sale. Everything a shopper sees is built here so that nothing internal (supplier, cost, margin, address, email, phone, payment reference)
can leak by accident: the shapes are explicit allow-lists.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.dropshipping import (
    Order,
    OrderLine,
    OrderStatus,
    Product,
    ProductStatus,
    Shipment,
    Sku,
    SkuAvailability,
    SkuStatus,
    SupplierOrder,
)
from app.services import dropshipping_shipping as shipping_rules
from app.services import dropshipping_tax as tax
from app.services.dropshipping_catalog_import import CURRENCY, CatalogValidationError, parse_money
from app.services.dropshipping_catalog_service import CatalogNotFoundError, _audit
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.tenant_settings_service import DROPSHIPPING_STORE, TenantSettingsService
from app.tools.builtin.stripe_tools import resolve_stripe_secret_key

SALES_TAX_NOTICE = "Sales tax is not collected on these orders."
SALES_TAX_NOTICE_STRIPE = "Sales tax is calculated at checkout from your delivery address."
STATUS_LABELS = {
    OrderStatus.PENDING_PAYMENT.value: "Awaiting payment",
    OrderStatus.PAID.value: "Paid. We are preparing your order.",
    OrderStatus.SUPPLIER_ORDER_PLACED.value: "Your order has been sent to our supplier.",
    OrderStatus.SHIPPED.value: "Shipped",
    OrderStatus.DELIVERED.value: "Delivered",
    OrderStatus.CANCELLED.value: "Cancelled",
    OrderStatus.REFUND_REQUESTED.value: "Refund in progress",
}


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def compute_shipping(settings: dict, subtotal: Decimal, weights: list[tuple[int | None, int]] | None = None) -> Decimal:
    """The owner's rule, applied on the server: weight tiers when the store has them (`weights` = each item's grams and quantity), otherwise a flat fee; free once the subtotal reaches
    the threshold, if one is set."""
    tiers, fee = settings.get("shipping_tiers"), settings.get("shipping_flat_fee")
    if not tiers and fee is None:
        raise CatalogValidationError(["the store has no shipping rule yet"])
    free_over = settings.get("free_shipping_over")
    if free_over is not None and subtotal >= Decimal(str(free_over)):
        return Decimal("0.00")
    if tiers:
        weight = shipping_rules.order_weight_lb(weights or [])
        if weight is None:
            raise CatalogValidationError(["Shipping for this order cannot be priced because an item in it has no weight. Please contact the store."])
        by_weight = shipping_rules.tier_fee(tiers, weight)
        if by_weight is None:
            raise CatalogValidationError([f"This order weighs {weight.quantize(Decimal('0.1'))} lb, which is more than the store ships in one order. Please split it into smaller orders."])
        return by_weight
    return _money(fee)


def shipping_text(settings: dict) -> str:
    fee, free_over, tiers = settings.get("shipping_flat_fee"), settings.get("free_shipping_over"), settings.get("shipping_tiers")
    if tiers:
        text = shipping_rules.describe(tiers)
        return text + (f" Free on orders of ${_money(free_over)} or more." if free_over is not None else "")
    if fee is None:
        return ""
    text = "Free shipping." if _money(fee) == 0 else f"Flat-rate shipping: ${_money(fee)}."
    if free_over is not None and _money(fee) != 0:
        text += f" Free on orders of ${_money(free_over)} or more."
    return text


class DropshippingStoreService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service
        self._settings = TenantSettingsService(session_factory)

    # --- settings and the open gate ------------------------------------------------------------------------------------------------------------------

    async def get_settings(self, tenant_id: uuid.UUID) -> dict:
        return await self._settings.get(tenant_id, DROPSHIPPING_STORE)

    async def blockers(self, tenant_id: uuid.UUID, settings: dict | None = None, *, include_switch: bool = False) -> list[str]:
        """Plain sentences for everything still missing before shoppers can buy. `include_switch` also reports the owner's open/closed switch."""
        s = settings if settings is not None else await self.get_settings(tenant_id)
        out: list[str] = []
        if include_switch and not s.get("open"):
            out.append("The store is switched off.")
        if s.get("shipping_flat_fee") is None and not s.get("shipping_tiers"):
            out.append("Set a shipping rule (a flat fee, which may be 0).")
        if (s.get("sales_tax_mode") or tax.MODE_NONE) == tax.MODE_NONE and not s.get("sales_tax_acknowledged"):
            out.append("Acknowledge that sales tax is not collected on orders, or collect it with Stripe Tax.")
        if not await resolve_stripe_secret_key(self._connection_service, tenant_id):
            out.append("Stripe is not connected (no connection and no platform key).")
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            sellable = (await session.execute(
                select(func.count()).select_from(Product).join(Sku, Sku.product_id == Product.id).where(
                    Product.tenant_id == tenant_id, Product.status == ProductStatus.ACTIVE, Sku.status == SkuStatus.ACTIVE)
            )).scalar_one()
        if not sellable:
            out.append("Publish at least one product with an active item.")
        if s.get("shipping_tiers"):
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                unweighed = (await session.execute(
                    select(func.count()).select_from(Product).join(Sku, Sku.product_id == Product.id).where(
                        Product.tenant_id == tenant_id, Product.status == ProductStatus.ACTIVE, Sku.status == SkuStatus.ACTIVE,
                        (Sku.weight_grams.is_(None)) | (Sku.weight_grams <= 0))
                )).scalar_one()
            if unweighed:
                out.append(f"{unweighed} item{' has' if unweighed == 1 else 's have'} no weight, so shipping by weight cannot be priced. Add each item's weight in the catalog.")
        return out

    async def is_open(self, tenant_id: uuid.UUID) -> tuple[bool, dict]:
        s = await self.get_settings(tenant_id)
        return (bool(s.get("open")) and not await self.blockers(tenant_id, s)), s

    async def update_settings(self, tenant_id: uuid.UUID, changes: dict, *, actor_id: uuid.UUID | None) -> dict:
        allowed = {"open", "shipping_flat_fee", "free_shipping_over", "shipping_tiers", "sales_tax_acknowledged", "sales_tax_mode", "tax_code"}
        unknown = sorted(set(changes) - allowed)
        problems = [f"unknown setting: {k}" for k in unknown]
        current = await self.get_settings(tenant_id)
        new = dict(current)
        for key in ("open", "sales_tax_acknowledged"):
            if key in changes:
                if not isinstance(changes[key], bool):
                    problems.append(f"{key} must be true or false")
                else:
                    new[key] = changes[key]
        if "shipping_tiers" in changes:
            if changes["shipping_tiers"] is None:
                new["shipping_tiers"] = None
            else:
                tiers, tier_problems = shipping_rules.normalize_tiers(changes["shipping_tiers"])
                problems.extend(tier_problems)
                if not tier_problems:
                    new["shipping_tiers"] = tiers
        if "sales_tax_mode" in changes:
            if changes["sales_tax_mode"] not in tax.MODES:
                problems.append("sales_tax_mode must be NONE or STRIPE_TAX")
            else:
                new["sales_tax_mode"] = changes["sales_tax_mode"]
        if "tax_code" in changes:
            if not isinstance(changes["tax_code"], str) or not tax.TAX_CODE_RE.match(changes["tax_code"]):
                problems.append("tax_code must look like txcd_40040000 (a Stripe product tax code)")
            else:
                new["tax_code"] = changes["tax_code"]
        for key in ("shipping_flat_fee", "free_shipping_over"):
            if key in changes:
                if changes[key] is None:
                    new[key] = None
                else:
                    try:
                        new[key] = str(parse_money(changes[key], key, allow_zero=(key == "shipping_flat_fee")))
                    except ValueError as exc:
                        problems.append(str(exc))
        if problems:
            raise CatalogValidationError(problems)
        if new.get("free_shipping_over") is not None and new.get("shipping_flat_fee") is None and not new.get("shipping_tiers"):
            raise CatalogValidationError(["free_shipping_over needs a shipping rule (a flat fee or weight tiers)"])
        if new.get("sales_tax_mode") == tax.MODE_STRIPE_TAX and current.get("sales_tax_mode") != tax.MODE_STRIPE_TAX:
            key = await resolve_stripe_secret_key(self._connection_service, tenant_id)
            if not key:
                raise CatalogValidationError(["Stripe is not connected, so Stripe Tax cannot be checked."])
            not_ready = await tax.check_ready(key)
            if not_ready:
                raise CatalogValidationError(["Stripe Tax is not ready yet:", *not_ready])
        if new.get("open") and not current.get("open"):
            remaining = await self.blockers(tenant_id, new)
            if remaining:
                raise CatalogValidationError(["The store cannot be switched on yet:", *remaining])
        if new.get("sales_tax_acknowledged") and not current.get("sales_tax_acknowledged"):
            new["acknowledged_by"], new["acknowledged_at"] = str(actor_id) if actor_id else None, datetime.now(timezone.utc).isoformat()
        elif not new.get("sales_tax_acknowledged"):
            new.pop("acknowledged_by", None), new.pop("acknowledged_at", None)
        saved = await self._settings.set(tenant_id, DROPSHIPPING_STORE, new, updated_by=actor_id)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            _audit(session, tenant_id, actor_id, "dropshipping.store.settings_changed", "dropshipping_store", tenant_id,
                   {"changed": sorted(k for k in allowed if new.get(k) != current.get(k)), "open": new.get("open")})
            await session.commit()
        return saved

    async def staff_view(self, tenant_id: uuid.UUID) -> dict:
        s = await self.get_settings(tenant_id)
        blockers = await self.blockers(tenant_id, s)
        return {"settings": s, "open_to_shoppers": bool(s.get("open")) and not blockers, "blockers": blockers}

    # --- what a shopper may see ----------------------------------------------------------------------------------------------------------------------

    async def public_info(self, tenant_id: uuid.UUID) -> dict:
        s = await self.get_settings(tenant_id)
        mode = s.get("sales_tax_mode") or tax.MODE_NONE
        return {"open": True, "currency": CURRENCY, "shipping": shipping_text(s), "free_shipping_over": s.get("free_shipping_over"),
                "flat_shipping_fee": s.get("shipping_flat_fee"), "shipping_tiers": s.get("shipping_tiers") or None, "sales_tax_mode": mode,
                "sales_tax_notice": SALES_TAX_NOTICE_STRIPE if mode == tax.MODE_STRIPE_TAX else SALES_TAX_NOTICE, "ships_to": "the continental United States"}

    @staticmethod
    def _public_product(product: Product, skus: list[Sku]) -> dict:
        return {
            "name": product.name, "slug": product.slug, "description": product.description, "category": product.category, "image_url": product.image_url,
            "non_perishable": bool(product.is_non_perishable),
            "items": [{"sku": k.sku_code, "price": str(_money(k.price)), "currency": k.currency, "in_stock": k.availability == SkuAvailability.AVAILABLE, "weight_g": k.weight_grams} for k in skus],
        }

    async def list_products(self, tenant_id: uuid.UUID, *, q: str | None = None, category: str | None = None, limit: int = 24, offset: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(Product).where(Product.tenant_id == tenant_id, Product.status == ProductStatus.ACTIVE)
            if q and q.strip():
                stmt = stmt.where(func.lower(Product.name).like(f"%{q.strip().lower()}%"))
            if category and category.strip():
                stmt = stmt.where(func.lower(Product.category) == category.strip().lower())
            products = list((await session.execute(stmt.order_by(Product.name).limit(limit).offset(max(0, int(offset))))).scalars())
            return await self._with_skus(session, tenant_id, products)

    async def get_product(self, tenant_id: uuid.UUID, slug: str) -> dict:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            product = (await session.execute(select(Product).where(Product.tenant_id == tenant_id, Product.slug == slug, Product.status == ProductStatus.ACTIVE))).scalar_one_or_none()
            out = await self._with_skus(session, tenant_id, [product]) if product is not None else []
        if not out:
            raise CatalogNotFoundError("Product not found")
        return out[0]

    async def _with_skus(self, session, tenant_id: uuid.UUID, products: list[Product]) -> list[dict]:
        if not products:
            return []
        skus = list((await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.product_id.in_([p.id for p in products]), Sku.status == SkuStatus.ACTIVE).order_by(Sku.sku_code))).scalars())
        # a product with no active item has nothing to sell, so it is not shown
        return [self._public_product(p, [k for k in skus if k.product_id == p.id]) for p in products if any(k.product_id == p.id for k in skus)]

    async def order_status(self, tenant_id: uuid.UUID, public_token: str) -> dict:
        settings = await self.get_settings(tenant_id)
        """The shopper's own order page. Looked up by its unguessable token inside the store's tenant; never returns the address, email, phone, payment reference or supplier."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            order = (await session.execute(select(Order).where(Order.tenant_id == tenant_id, Order.public_token == public_token))).scalar_one_or_none()
            if order is None or order.personal_data_erased_at is not None:
                raise CatalogNotFoundError("Order not found")
            lines = list((await session.execute(select(OrderLine).where(OrderLine.tenant_id == tenant_id, OrderLine.order_id == order.id))).scalars())
            sos = list((await session.execute(select(SupplierOrder).where(SupplierOrder.tenant_id == tenant_id, SupplierOrder.order_id == order.id))).scalars())
            shipments = list((await session.execute(select(Shipment).where(Shipment.tenant_id == tenant_id, Shipment.supplier_order_id.in_([s.id for s in sos])))).scalars()) if sos else []
            from app.services.dropshipping_problem_service import DropshippingProblemService, can_report  # late: that module imports the order service

            problem = await DropshippingProblemService(self._session_factory).status_for_order(session, tenant_id, order.id)
            offer_report = can_report(order, problem in ("PENDING", "APPROVED"))
        return {
            "order_number": order.order_number, "status": order.status, "status_label": STATUS_LABELS.get(order.status, order.status), "currency": order.currency,
            "placed_at": order.placed_at.isoformat() if order.placed_at else None,
            "subtotal": str(_money(order.subtotal)), "shipping": str(_money(order.shipping)), "tax": str(_money(order.tax)), "total": str(_money(order.total)),
            "can_pay": order.status == OrderStatus.PENDING_PAYMENT.value,
            "can_report_problem": offer_report, "problem": {"status": problem} if problem else None,
            "tax_at_payment": order.status == OrderStatus.PENDING_PAYMENT.value and (settings.get("sales_tax_mode") or tax.MODE_NONE) == tax.MODE_STRIPE_TAX and Decimal(order.tax) == 0,
            "items": [{"name": ln.name_snapshot, "quantity": ln.quantity, "unit_price": str(_money(ln.unit_price)), "line_total": str(_money(ln.line_total))} for ln in lines],
            "tracking": [{"carrier": sh.carrier, "tracking_number": sh.tracking_number, "tracking_url": sh.tracking_url, "status": sh.status} for sh in shipments],
        }
