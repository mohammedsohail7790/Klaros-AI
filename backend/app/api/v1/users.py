import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.user import User
from app.schemas.auth import UserResponse
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> UserResponse:
    # Tenant isolation: always filter by tenant_id derived from the JWT, never from input.
    user = (
        await db.execute(
            select(User).where(User.id == current_user.id, User.tenant_id == current_user.tenant_id)
        )
    ).scalar_one()
    return UserResponse.model_validate(user)


@router.get("")
async def list_members(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("team.list_members", {}, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class UpdateMemberRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


@router.patch("/{user_id}")
async def update_member(
    user_id: uuid.UUID,
    body: UpdateMemberRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"user_id": str(user_id), "role": body.role, "is_active": body.is_active}
    try:
        output = await registry.execute("team.update_member", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class CreateInviteRequest(BaseModel):
    email: str
    role: str


@router.post("/invites", status_code=201)
async def create_invite(
    body: CreateInviteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "team.create_invite", {"email": body.email, "role": body.role}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/invites")
async def list_invites(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("team.list_invites", {}, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(
    invite_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "team.revoke_invite", {"invite_id": str(invite_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
