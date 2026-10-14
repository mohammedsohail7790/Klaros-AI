"""`/api/v1/setup-plan`: what it takes to run this business on Klaros, and one call that does everything Klaros can do on its own. Same for every business."""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, require_permission
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services import setup_plan

router = APIRouter(prefix="/setup-plan", tags=["setup-plan"])


@router.get("")
async def get_setup_plan(current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY))) -> dict[str, Any]:
    return await setup_plan.build_plan(async_session_maker, current_user.tenant_id)


@router.post("/activate")
async def activate_setup_plan(current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY))) -> dict[str, Any]:
    return await setup_plan.activate(async_session_maker, current_user.tenant_id, current_user.id)
