from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.models.rbac import Role
from app.services import business_rename
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("/kill-switch")
async def get_kill_switch(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("organization.get_kill_switch_status", {}, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class SetKillSwitchRequest(BaseModel):
    active: bool


@router.post("/kill-switch")
async def set_kill_switch(
    body: SetKillSwitchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "organization.set_kill_switch", {"active": body.active}, execution_context(current_user)
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class RenameRequest(BaseModel):
    name: str


@router.put("/name")
async def rename_organization(body: RenameRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Change the business's name everywhere it was recorded (organisation, Medical Tourism profile, Business Blueprint, content topics). Only the owner can."""
    if current_user.role != Role.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can change the business name.")
    try:
        return await business_rename.rename_business(async_session_maker, current_user.tenant_id, current_user.id, body.name)
    except business_rename.RenameError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/name")
async def get_organization_name(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.db.session import set_tenant_context
    from app.models.organization import Organization

    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        org = await session.get(Organization, current_user.tenant_id)
    return {"name": org.name if org else ""}
