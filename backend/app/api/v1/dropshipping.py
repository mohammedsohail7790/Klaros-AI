"""Dropshipping store, branch 2: the staff catalog API, `/api/v1/dropshipping/*`.

Follows the medical-tourism router's convention: explicit typed routes, writes that an AI agent may also perform go through `ToolRegistry.execute()` (permission,
tenant, schema, policy and audit on every call), and reads go through the service. Two operations are deliberately NOT routed through the registry:

  - publishing a product and committing a CSV import. The registry applies a tool's APPROVAL_REQUIRED policy to every actor, a person included, and these two are
    APPROVAL_REQUIRED for AI agents. A staff member holding MANAGE_DROPSHIPPING is the approver, so these call the service directly (as medical tourism's
    credential routes do) and the service writes its own audit row in the same transaction as the change.

Nothing here is public: responses include supplier cost and margin. The public storefront API arrives in a later branch.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.models.rbac import Permission, role_has_permission
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import (
    CatalogConflictError,
    CatalogNotFoundError,
    DropshippingCatalogService,
    ImportRejectedError,
    ImportTokenError,
    PublishBlockedError,
    supplier_to_dict,
)
from app.services.dropshipping_sync_service import DropshippingSyncService
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/dropshipping", tags=["dropshipping"])


def _service() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


def _sync_service() -> DropshippingSyncService:
    return DropshippingSyncService(async_session_maker)


def _require(current_user: CurrentUser, permission: Permission) -> None:
    if current_user.role is None or not role_has_permission(current_user.role, permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission.value}")


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ImportRejectedError):
        return HTTPException(status_code=422, detail={"message": str(exc), "report": exc.report.to_dict()})
    if isinstance(exc, CatalogValidationError):  # includes PublishBlockedError
        return HTTPException(status_code=422, detail={"message": "The request was refused.", "problems": exc.problems})
    if isinstance(exc, (CatalogConflictError, ImportTokenError)):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


# --- suppliers -----------------------------------------------------------------------------------------------------------------------------------------


@router.get("/suppliers")
async def list_suppliers(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _require(current_user, Permission.READ_DROPSHIPPING)
    return {"suppliers": [supplier_to_dict(s) for s in await _service().list_suppliers(current_user.tenant_id)]}


@router.post("/suppliers")
async def create_supplier(
    body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    """body = {name, integration_type? (MANUAL), contact_email?, contact_phone?, notes?}. A supplier with the same name is returned (deduplicated)."""
    try:
        output = await registry.execute("dropshipping.create_supplier", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/orders/lookup")
async def lookup_order_for_customer(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """'Where is my order?' for an integration (the voice receptionist, a chat widget) acting for a customer who is not signed in.
    body = {order_number, email? | phone?}: the customer must give the order number and the e-mail or phone it was placed with. A wrong or unknown pair always gets found=false."""
    _require(current_user, Permission.READ_DROPSHIPPING)
    try:
        output = await registry.execute("dropshipping.lookup_order", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/digest/preview")
async def preview_digest(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """The owner digest as it would be written right now (nothing is sent). Contains no customer details."""
    _require(current_user, Permission.READ_DROPSHIPPING)
    from app.core.config import get_settings
    from app.services import dropshipping_digest as digest

    data = await digest.gather(async_session_maker, current_user.tenant_id)
    subject, body = digest.compose(data, get_settings().FRONTEND_BASE_URL)
    return {"subject": subject, "body": body, "quiet_day": digest.is_quiet(data), "recipients": len(data["owners"]), "already_sent_today": data["already_sent_today"]}


@router.post("/digest/send")
async def send_digest_now(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Send today's digest to the owners now. Still at most once a day, and still skipped on a quiet day or when no e-mail provider is set; the answer says why."""
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    from app.services import dropshipping_digest as digest

    return await digest.send_store_digest(async_session_maker, current_user.tenant_id)


@router.put("/suppliers/{supplier_id}/sync-settings")
async def save_sync_settings(supplier_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """The daily stock and price sync of one supplier. body = {feed_url? (https), markup_percent? (0-500; none = prices are never touched), stock_buffer? (0), max_price_change_percent? (30),
    sync_enabled? (needs a feed_url)}. Staff with MANAGE_DROPSHIPPING only."""
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    try:
        return await _sync_service().save_settings(current_user.tenant_id, supplier_id, body, actor_id=current_user.id)
    except (CatalogNotFoundError, CatalogValidationError) as exc:
        raise _http_for(exc) from exc


@router.post("/suppliers/{supplier_id}/sync")
async def run_supplier_sync(supplier_id: uuid.UUID, body: dict | None = None, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run the sync now. body = {csv_text? (paste a feed instead of fetching the link), dry_run? (default true: a preview that changes nothing), force? (apply even the held stock changes and big price moves)}."""
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    body = body or {}
    try:
        return await _sync_service().run(current_user.tenant_id, supplier_id, csv_text=body.get("csv_text"), dry_run=body.get("dry_run", True) is not False, force=body.get("force") is True, actor_id=current_user.id)
    except (CatalogNotFoundError, CatalogValidationError) as exc:
        raise _http_for(exc) from exc


# --- products --------------------------------------------------------------------------------------------------------------------------------------------


@router.get("/products")
async def list_products(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, pattern="^(DRAFT|ACTIVE|ARCHIVED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require(current_user, Permission.READ_DROPSHIPPING)
    return {"products": await _service().search_products(current_user.tenant_id, q=q, status=status, limit=limit, offset=offset)}


@router.post("/products")
async def upsert_product(
    body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    """Create or update a product and its SKUs (matched by slug and SKU code); the product stays a DRAFT.
    body = {name, skus: [{sku_code, price, supplier, supplier_sku?, supplier_cost?, currency?, weight_grams?, availability?}], slug?, description?, category?, image_url?, is_non_perishable?}"""
    try:
        output = await registry.execute("dropshipping.upsert_product", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/products/{product_id}")
async def get_product(product_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _require(current_user, Permission.READ_DROPSHIPPING)
    try:
        return {"product": await _service().get_product(current_user.tenant_id, product_id)}
    except CatalogNotFoundError as exc:
        raise _http_for(exc)


@router.post("/products/{product_id}/publish")
async def publish_product(product_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Puts a DRAFT product on sale, or refuses with the list of reasons. A person with MANAGE_DROPSHIPPING approves it by calling this."""
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    try:
        return {"product": await _service().publish_product(current_user.tenant_id, product_id, actor_id=current_user.id)}
    except (CatalogNotFoundError, CatalogValidationError) as exc:
        raise _http_for(exc)


@router.post("/products/{product_id}/archive")
async def archive_product(product_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    try:
        return {"product": await _service().archive_product(current_user.tenant_id, product_id, actor_id=current_user.id)}
    except CatalogNotFoundError as exc:
        raise _http_for(exc)


@router.patch("/skus/{sku_id}/availability")
async def set_availability(
    sku_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    """body = {availability: AVAILABLE | OUT_OF_STOCK | UNKNOWN, quantity?}"""
    try:
        output = await registry.execute("dropshipping.set_availability", {**body, "sku_id": str(sku_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


# --- CSV import ----------------------------------------------------------------------------------------------------------------------------------------


@router.post("/catalog/import/preview")
async def preview_import(body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """body = {csv_text}. Validates the whole file and reports what a commit would do. Writes NOTHING. The `token` in the answer is needed to commit this exact file."""
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    report = await _service().preview_import(current_user.tenant_id, body.get("csv_text") if isinstance(body, dict) else None)
    return report.to_dict()


@router.post("/catalog/import/commit")
async def commit_import(body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """body = {csv_text, token}. Commits exactly the previewed file, all or nothing; products land as DRAFT. A person with MANAGE_DROPSHIPPING approves it by calling this."""
    _require(current_user, Permission.MANAGE_DROPSHIPPING)
    try:
        report = await _service().commit_import(
            current_user.tenant_id, body.get("csv_text") or "", body.get("token") or "", actor_id=current_user.id
        )
    except (ImportTokenError, ImportRejectedError, CatalogConflictError) as exc:
        raise _http_for(exc)
    return report.to_dict()
