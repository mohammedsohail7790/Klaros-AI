"""Dropshipping store, branch 4: staff view and control of when the public storefront is open, `/api/v1/dropshipping/store-settings`.

READ_DROPSHIPPING may look (including what is still blocking the store from opening); MANAGE_DROPSHIPPING may change the shipping rule, the sales-tax acknowledgement and
the open/closed switch. Each change writes an audit row. Switching the store on is refused while anything that makes selling safe is missing.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps_integrations import get_integration_connection_service
from app.db.session import async_session_maker
from app.models.rbac import Permission, role_has_permission
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_store_service import DropshippingStoreService

router = APIRouter(prefix="/dropshipping", tags=["dropshipping-store"])


def _service() -> DropshippingStoreService:
    return DropshippingStoreService(async_session_maker, get_integration_connection_service())


def _need(user: CurrentUser, permission: Permission) -> None:
    if user.role is None or not role_has_permission(user.role, permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission.name}")


@router.get("/store-settings")
async def get_store_settings(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _need(current_user, Permission.READ_DROPSHIPPING)
    return await _service().staff_view(current_user.tenant_id)


@router.put("/store-settings")
async def put_store_settings(body: dict, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """body may contain: open (bool), shipping_flat_fee (money or null), free_shipping_over (money or null), sales_tax_acknowledged (bool)."""
    _need(current_user, Permission.MANAGE_DROPSHIPPING)
    try:
        await _service().update_settings(current_user.tenant_id, body, actor_id=current_user.id)
    except CatalogValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": "The request was refused.", "problems": exc.problems})
    return await _service().staff_view(current_user.tenant_id)
