"""Dropshipping store: "tell me when it is back" for an item that is sold out.

A shopper leaves an e-mail address for one sold-out item. When the item is available again (the daily supplier sync or staff set it), they get ONE plain message with the link to the
product, and their address is removed from the record at the same moment, so it is never kept longer than needed. A service message the shopper asked for, not marketing: no
promotion, no tracking. Nothing is sent while the store is closed; a message the provider did not accept is retried on the next pass; nothing is stored for an item that is in stock,
unknown, or not on sale; the same address asking twice for the same item is a no-op; and each item holds at most 500 open requests.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.dropshipping import Product, ProductStatus, Sku, SkuAvailability, SkuStatus, StockAlert
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError
from app.services.system_mailer import send_system_email

logger = structlog.get_logger(__name__)

MAX_OPEN_PER_SKU = 500
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def build_email(product_name: str, *, store_name: str, url: str) -> tuple[str, str]:
    subject = f"{store_name}: {product_name} is back in stock"
    body = (f"Hello,\n\nYou asked us to tell you when {product_name} is back. It is available again:\n{url}\n\n"
            f"Stock can run out again, so this is not a reservation. We will not e-mail you again about it.\n\n{store_name}\n")
    return subject, body


async def subscribe(session_factory, tenant_id: uuid.UUID, sku_code: object, email: object) -> None:
    address = str(email or "").strip().lower()
    if not _EMAIL.match(address) or len(address) > 255:
        raise CatalogValidationError(["enter a valid e-mail address"])
    code = str(sku_code or "").strip()[:80]
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(select(Sku, Product.status).join(Product, Product.id == Sku.product_id).where(Sku.tenant_id == tenant_id, Sku.sku_code == code))).first()
        if row is None or row[0].status != SkuStatus.ACTIVE.value or row[1] != ProductStatus.ACTIVE.value:
            raise CatalogNotFoundError("Item not found")
        sku = row[0]
        if sku.availability == SkuAvailability.AVAILABLE.value:
            raise CatalogConflictError("This item is in stock now.")
        again = (await session.execute(select(StockAlert.id).where(StockAlert.tenant_id == tenant_id, StockAlert.sku_id == sku.id, StockAlert.email == address, StockAlert.notified_at.is_(None)).limit(1))).first()
        if again is not None:
            return  # already on the list: the answer is the same as for a new one
        open_count = (await session.execute(select(func.count()).select_from(StockAlert).where(StockAlert.tenant_id == tenant_id, StockAlert.sku_id == sku.id, StockAlert.notified_at.is_(None)))).scalar_one()
        if open_count >= MAX_OPEN_PER_SKU:
            raise CatalogConflictError("Too many people are already waiting for this item.")
        session.add(StockAlert(tenant_id=tenant_id, sku_id=sku.id, email=address))
        await session.commit()


async def send_due(session_factory, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """One pass: tells everyone waiting for an item that is available again; returns how many messages the provider accepted. Never raises."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    try:
        from app.api.tool_deps_integrations import get_integration_connection_service  # late: the API layer imports the services
        from app.services.dropshipping_checkout_service import store_base_url
        from app.services.dropshipping_store_service import DropshippingStoreService

        if not (await DropshippingStoreService(session_factory, get_integration_connection_service()).is_open(tenant_id))[0]:
            return 0
        store_name = get_settings().DROPSHIPPING_STORE_NAME
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (await session.execute(select(StockAlert, Product.name, Product.slug).join(Sku, Sku.id == StockAlert.sku_id).join(Product, Product.id == Sku.product_id).where(
                StockAlert.tenant_id == tenant_id, StockAlert.notified_at.is_(None), StockAlert.email.is_not(None), Sku.availability == SkuAvailability.AVAILABLE.value,
                Sku.status == SkuStatus.ACTIVE.value, Product.status == ProductStatus.ACTIVE.value).order_by(StockAlert.created_at).limit(200))).all()
            for alert, name, slug in rows:
                subject, body = build_email(name, store_name=store_name, url=f"{store_base_url()}/store/products/{slug}")
                try:
                    ok = await send_system_email(to=alert.email, subject=subject, body=body, from_name=store_name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("dropshipping_stock_alert_failed", error_type=type(exc).__name__)
                    ok = False
                if ok:
                    alert.email, alert.notified_at = None, now      # told: the address is not kept
                    await session.commit()
                    sent += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("dropshipping_stock_alerts_failed", error_type=type(exc).__name__)
    return sent


async def run_stock_alert_sweeper(session_factory, tenant_id_getter, stop: "asyncio.Event", every_seconds: float = 1800.0) -> None:
    """Background loop: every 30 minutes, tells the people waiting for items that are available again."""
    while not stop.is_set():
        try:
            tenant_id = tenant_id_getter()
            if tenant_id is not None:
                await send_due(session_factory, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dropshipping_stock_alert_sweep_failed", error_type=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_seconds)
        except asyncio.TimeoutError:
            pass
