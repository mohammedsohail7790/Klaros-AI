from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
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
