"""Dropshipping store, branch 3: the staff order API, `/api/v1/dropshipping/*` (orders, supplier orders, shipments).

Staff-only and not public: responses carry the ship-to address, contact details and payment reference, which staff need to fulfil an order. Every route calls
`DropshippingOrderService` directly behind a permission check; the service writes its own audit row in the same transaction as each change (no AI agent performs
these actions, so none goes through ToolRegistry).

Permissions: READ_DROPSHIPPING to look; COORDINATE_DROPSHIPPING (or MANAGE) to work orders (take a phone order, place supplier orders, add tracking, deliver,
cancel, flag a refund); MANAGE_DROPSHIPPING alone to record a payment and to erase an order's personal data.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.models.rbac import Permission, role_has_permission
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogConflictError, CatalogNotFoundError
from app.services.dropshipping_order_export import supplier_orders_csv
from app.services.dropshipping_order_rules import OrderTransitionError
from app.services.dropshipping_order_service import DropshippingOrderService
from app.services.dropshipping_problem_service import DropshippingProblemService

router = APIRouter(prefix="/dropshipping", tags=["dropshipping-orders"])


def _service() -> DropshippingOrderService:
    return DropshippingOrderService(async_session_maker)


def _has(user: CurrentUser, *permissions: Permission) -> bool:
    return user.role is not None and any(role_has_permission(user.role, p) for p in permissions)


def _need_read(user: CurrentUser) -> None:
    if not _has(user, Permission.READ_DROPSHIPPING):
        raise HTTPException(status_code=403, detail="Missing permission: READ_DROPSHIPPING")


def _need_coordinate(user: CurrentUser) -> None:
    if not _has(user, Permission.COORDINATE_DROPSHIPPING, Permission.MANAGE_DROPSHIPPING):
        raise HTTPException(status_code=403, detail="Missing permission: COORDINATE_DROPSHIPPING")


def _need_manage(user: CurrentUser) -> None:
    if not _has(user, Permission.MANAGE_DROPSHIPPING):
        raise HTTPException(status_code=403, detail="Missing permission: MANAGE_DROPSHIPPING")


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CatalogValidationError):
        return HTTPException(status_code=422, detail={"message": "The request was refused.", "problems": exc.problems})
    if isinstance(exc, (OrderTransitionError, CatalogConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


_HANDLED = (CatalogNotFoundError, CatalogValidationError, OrderTransitionError, CatalogConflictError)


# --- orders --------------------------------------------------------------------------------------------------------------------------------------------


@router.get("/orders")
async def list_orders(
    q: str | None = Query(default=None, max_length=40),
    status: str | None = Query(default=None, pattern="^(PENDING_PAYMENT|PAID|SUPPLIER_ORDER_PLACED|SHIPPED|DELIVERED|CANCELLED|REFUND_REQUESTED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _need_read(current_user)
    return {"orders": await _service().search_orders(current_user.tenant_id, status=status, q=q, limit=limit, offset=offset)}


@router.get("/orders/{order_id}")
async def get_order(order_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _need_read(current_user)
    try:
        return {"order": await _service().get_order(current_user.tenant_id, order_id)}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/orders")
async def create_order(body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Take an order on behalf of a customer (for example by phone). body = {lines: [{sku, quantity}], contact: {name, email?, phone?}, ship_to: {name, line1, line2?, city, state, postal_code},
    idempotency_key, shipping?}. The same idempotency_key with the same content returns the existing order (created: false); with different content it is a 409."""
    _need_coordinate(current_user)
    try:
        order, created = await _service().create_order(
            current_user.tenant_id, lines=body.get("lines"), contact=body.get("contact"), ship_to=body.get("ship_to"),
            idempotency_key=body.get("idempotency_key") or "", shipping=body.get("shipping", "0"), actor_id=current_user.id,
        )
    except _HANDLED as exc:
        raise _http_for(exc)
    return {"order": order, "created": created}


@router.post("/orders/{order_id}/mark-paid")
async def mark_paid(order_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Record a payment received outside Stripe (for example a bank transfer). body = {payment_reference}: required, and it makes the call idempotent.
    A card payment through the store is recorded automatically by the checkout, not here."""
    _need_manage(current_user)
    try:
        return {"order": await _service().mark_paid(current_user.tenant_id, order_id, payment_reference=body.get("payment_reference"), source="MANUAL", actor_id=current_user.id)}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """body = {reason}. Unpaid -> CANCELLED. Paid with nothing placed with a supplier -> REFUND_REQUESTED (Klaros moves no money: refund in Stripe)."""
    _need_coordinate(current_user)
    try:
        return {"order": await _service().cancel_order(current_user.tenant_id, order_id, reason=body.get("reason"), actor_id=current_user.id)}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/orders/{order_id}/request-refund")
async def request_refund(order_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """body = {reason}. Flags a paid order as needing money returned. Klaros does not refund."""
    _need_coordinate(current_user)
    try:
        return {"order": await _service().request_refund(current_user.tenant_id, order_id, reason=body.get("reason"), actor_id=current_user.id)}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/orders/{order_id}/erase-personal-data")
async def erase_personal_data(order_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Clears the order's email, phone and address and its public status link, once the order is finished. Totals, lines and tracking stay."""
    _need_manage(current_user)
    try:
        return {"order": await _service().erase_personal_data(current_user.tenant_id, order_id, actor_id=current_user.id)}
    except _HANDLED as exc:
        raise _http_for(exc)


# --- supplier orders (the work queue) and tracking ---------------------------------------------------------------------------------------------------------


@router.get("/supplier-orders")
async def list_supplier_orders(
    status: str | None = Query(default=None, pattern="^(TO_PLACE|PLACED|CONFIRMED|FAILED|CANCELLED)$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """What to order from each supplier, with the items and where they ship. Default view for staff: status=TO_PLACE."""
    _need_read(current_user)
    return {"supplier_orders": await _service().list_supplier_orders(current_user.tenant_id, status=status, limit=limit, offset=offset)}


@router.get("/supplier-orders/export")
async def export_supplier_orders(
    status: str = Query(default="TO_PLACE", pattern="^(TO_PLACE|PLACED|CONFIRMED|FAILED|CANCELLED)$"), current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """The queue as a CSV (one row per item to order, with the delivery address) to hand to the supplier. A bulk file of addresses: staff who work orders only."""
    _need_coordinate(current_user)
    entries = await _service().list_supplier_orders(current_user.tenant_id, status=status, limit=200)
    return Response(content=supplier_orders_csv(entries), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="supplier-orders-{status.lower()}.csv"', "Cache-Control": "no-store"})


@router.get("/problem-reports")
async def list_problem_reports(
    status: str | None = Query(default=None, pattern="^(PENDING|APPROVED|REJECTED)$"), limit: int = Query(default=100, ge=1, le=200), current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """What shoppers reported as wrong with their orders (damaged, wrong or missing items). Default view for staff: status=PENDING."""
    _need_read(current_user)
    return {"problem_reports": await DropshippingProblemService(async_session_maker).list(current_user.tenant_id, status=status, limit=limit)}


@router.post("/problem-reports/{report_id}/{decision}")
async def decide_problem_report(report_id: uuid.UUID, decision: str, body: dict | None = None, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """decision = approve (flags the order for a refund; Klaros moves no money, staff refund in Stripe) | reject (body: note, required: why). Staff who work orders only."""
    _need_coordinate(current_user)
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="Unknown decision")
    try:
        return {"problem_report": await DropshippingProblemService(async_session_maker).decide(
            current_user.tenant_id, report_id, approve=decision == "approve", note=(body or {}).get("note"), actor_id=current_user.id)}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/supplier-orders/{supplier_order_id}/shipments")
async def add_shipment(supplier_order_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """body = {carrier?, tracking_number, tracking_url? (https)}. The order becomes SHIPPED once every supplier order has a shipment.
    (Registered before the `{action}` route below, which would otherwise capture the word "shipments".)"""
    _need_coordinate(current_user)
    try:
        return {"order": await _service().add_shipment(
            current_user.tenant_id, supplier_order_id, carrier=body.get("carrier"), tracking_number=body.get("tracking_number"),
            tracking_url=body.get("tracking_url"), actor_id=current_user.id,
        )}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/supplier-orders/{supplier_order_id}/{action}")
async def mark_supplier_order(
    supplier_order_id: uuid.UUID, action: str, body: dict | None = None, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    """action = placed (body: supplier_reference, note?) | confirmed | failed (body: note). The order becomes SUPPLIER_ORDER_PLACED once all its supplier orders are placed."""
    _need_coordinate(current_user)
    if action not in ("placed", "confirmed", "failed"):
        raise HTTPException(status_code=404, detail="Unknown action")
    body = body or {}
    try:
        return {"order": await _service().mark_supplier_order(
            current_user.tenant_id, supplier_order_id, action, supplier_reference=body.get("supplier_reference"), note=body.get("note"), actor_id=current_user.id
        )}
    except _HANDLED as exc:
        raise _http_for(exc)


@router.post("/shipments/{shipment_id}/{status}")
async def mark_shipment(
    shipment_id: uuid.UUID, status: str, body: dict | None = None, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    """status = delivered | exception (body: note, required for an exception). The order becomes DELIVERED once every shipment is delivered."""
    _need_coordinate(current_user)
    if status not in ("delivered", "exception"):
        raise HTTPException(status_code=404, detail="Unknown status")
    try:
        return {"order": await _service().mark_shipment(current_user.tenant_id, shipment_id, status, note=(body or {}).get("note"), actor_id=current_user.id)}
    except _HANDLED as exc:
        raise _http_for(exc)
