from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/automation", tags=["automation"])


def _raise_for_policy_error(exc: Exception) -> None:
    from fastapi import HTTPException, status

    from app.services.policy_service import SystemBlockedPolicyError

    if isinstance(exc, SystemBlockedPolicyError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    raise_http_for_tool_error(exc)


@router.get("/autonomy-stats")
async def get_autonomy_stats(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("automation.get_autonomy_stats", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/policies")
async def list_policies(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("automation.list_policies", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/policies/{tool_name}")
async def get_policy(
    tool_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "automation.get_policy", {"tool_name": tool_name}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class SetPolicyRequest(BaseModel):
    policy: str


@router.put("/policies/{tool_name}")
async def set_policy(
    tool_name: str,
    body: SetPolicyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "automation.set_policy",
            {"tool_name": tool_name, "policy": body.policy},
            execution_context(current_user),
        )
    except Exception as exc:  # noqa: BLE001 — dispatched to a precise status code below
        _raise_for_policy_error(exc)
    return output.model_dump(mode="json")


class ResetPolicyRequest(BaseModel):
    tool_name: str


@router.post("/policies/reset")
async def reset_policy(
    body: ResetPolicyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "automation.reset_policy", {"tool_name": body.tool_name}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
