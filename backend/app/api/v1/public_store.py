"""Dropshipping store, branch 4: the PUBLIC storefront API, `/api/v1/public/store/*`. No login.

One business only: the tenant is `Settings.DROPSHIPPING_STORE_TENANT_ID`, never anything the caller sends, so no other tenant can be served here and an unset value means the
store does not exist (404). A store that is switched off, or is missing its shipping rule / tax acknowledgement / Stripe / products, answers 404 and reveals nothing.

What a shopper can send is deliberately small and strictly validated (unknown fields are refused, so a price or shipping amount cannot even be offered): items by code and
quantity, contact, ship-to address, an idempotency key. Prices come from the catalog and shipping from the owner's rule. Redirect URLs are built on the server from
FRONTEND_BASE_URL. What a shopper receives never includes the supplier, cost, margin, address, email, phone or payment reference. Stripe errors are not echoed.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.tool_deps_integrations import get_integration_connection_service
from app.core.config import get_settings
from app.core.rate_limit import rate_limit
from app.db.session import async_session_maker
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError
from app.services.dropshipping_checkout_service import (
    DropshippingCheckoutService,
    OrderNotPayableError,
    StoreCheckoutError,
    StoreClosedError,
    TooManyUnpaidOrdersError,
)
from app.services.dropshipping_order_rules import OrderTransitionError
from app.services.dropshipping_store_service import DropshippingStoreService
from app.services.quote_deposit_service import StripeNotConfiguredError

router = APIRouter(prefix="/public/store", tags=["public-store"])

_browse = Depends(rate_limit("public_store_browse", limit_setting="RATE_LIMIT_PUBLIC_STORE_BROWSE_PER_MINUTE", window_seconds=60))
_status = Depends(rate_limit("public_store_status", limit_setting="RATE_LIMIT_PUBLIC_STORE_STATUS_PER_MINUTE", window_seconds=60))
_checkout = Depends(rate_limit("public_store_checkout", limit_setting="RATE_LIMIT_PUBLIC_STORE_CHECKOUT_PER_MINUTE", window_seconds=60))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutLine(_Strict):
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=99)


class CheckoutBody(_Strict):
    lines: list[CheckoutLine] = Field(min_length=1, max_length=50)
    contact: dict[str, Any]
    ship_to: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=100)


def _store() -> DropshippingStoreService:
    return DropshippingStoreService(async_session_maker, get_integration_connection_service())


def _checkout_service() -> DropshippingCheckoutService:
    return DropshippingCheckoutService(async_session_maker, get_integration_connection_service())


def _tenant() -> uuid.UUID:
    raw = get_settings().DROPSHIPPING_STORE_TENANT_ID
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Not found") from None


async def _open_tenant() -> uuid.UUID:
    tenant_id = _tenant()
    is_open, _ = await _store().is_open(tenant_id)
    if not is_open:
        raise HTTPException(status_code=404, detail="Not found")
    return tenant_id


def _refuse(exc: Exception) -> HTTPException:
    if isinstance(exc, (StoreClosedError, CatalogNotFoundError)):
        return HTTPException(status_code=404, detail="Not found")
    if isinstance(exc, CatalogValidationError):
        return HTTPException(status_code=422, detail={"message": "The request was refused.", "problems": exc.problems})
    if isinstance(exc, (CatalogConflictError, OrderNotPayableError, OrderTransitionError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, StripeNotConfiguredError):
        return HTTPException(status_code=503, detail="Payments are not available right now")
    if isinstance(exc, TooManyUnpaidOrdersError):
        return HTTPException(status_code=429, detail="The store is busy, please try again later", headers={"Retry-After": "300"})
    if isinstance(exc, StoreCheckoutError):
        # never leak the underlying Stripe message to an unauthenticated caller
        return HTTPException(status_code=502, detail="Payment provider error")
    raise exc


_HANDLED = (StoreClosedError, CatalogNotFoundError, CatalogValidationError, CatalogConflictError, OrderNotPayableError, OrderTransitionError, StripeNotConfiguredError,
            TooManyUnpaidOrdersError, StoreCheckoutError)


@router.get("", dependencies=[_browse])
async def store_info() -> dict[str, Any]:
    return await _store().public_info(await _open_tenant())


@router.get("/products", dependencies=[_browse])
async def list_products(
    q: str | None = Query(default=None, max_length=80), category: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=24, ge=1, le=100), offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return {"products": await _store().list_products(await _open_tenant(), q=q, category=category, limit=limit, offset=offset)}


@router.get("/products/{slug}", dependencies=[_browse])
async def get_product(slug: str) -> dict[str, Any]:
    tenant_id = await _open_tenant()
    try:
        return {"product": await _store().get_product(tenant_id, slug[:160])}
    except _HANDLED as exc:
        raise _refuse(exc)


@router.post("/checkout", dependencies=[_checkout])
async def checkout(body: CheckoutBody) -> dict[str, Any]:
    """Creates the order and the Stripe Checkout Session. Returns {order_number, status_token, status, total, checkout_url}; send the shopper to `checkout_url`.
    Repeating the same idempotency_key with the same content returns the same order (and a Stripe session if it is still unpaid)."""
    tenant_id = _tenant()
    try:
        return await _checkout_service().checkout(
            tenant_id, lines=[ln.model_dump() for ln in body.lines], contact=body.contact, ship_to=body.ship_to, idempotency_key=body.idempotency_key,
        )
    except _HANDLED as exc:
        raise _refuse(exc)


@router.get("/orders/{status_token}", dependencies=[_status])
async def order_status(status_token: str) -> dict[str, Any]:
    tenant_id = _tenant()
    try:
        return {"order": await _store().order_status(tenant_id, status_token[:80])}
    except _HANDLED as exc:
        raise _refuse(exc)


@router.post("/orders/{status_token}/pay", dependencies=[_checkout])
async def pay_again(status_token: str) -> dict[str, Any]:
    tenant_id = _tenant()
    try:
        return await _checkout_service().pay_again(tenant_id, status_token[:80])
    except _HANDLED as exc:
        raise _refuse(exc)
