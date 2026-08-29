from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.models.organization import Organization
from app.models.rbac import Permission
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/morning-brief", tags=["morning-brief"])


@router.get("/latest")
async def get_latest_morning_brief(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("insights.get_latest_morning_brief", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/generate")
async def generate_morning_brief(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("insights.generate_morning_brief", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class ExecuteRecommendationRequest(BaseModel):
    pass


@router.post("/recommendations/{recommendation_id}/execute")
async def execute_recommendation(
    recommendation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "insights.execute_recommendation",
            {"recommendation_id": recommendation_id},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/recommendations/{recommendation_id}/dismiss")
async def dismiss_recommendation(
    recommendation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "insights.dismiss_recommendation",
            {"recommendation_id": recommendation_id},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class MorningBriefSettingsResponse(BaseModel):
    enabled: bool
    local_time: str
    timezone: str


class UpdateMorningBriefSettingsRequest(BaseModel):
    enabled: bool
    local_time: str
    timezone: str


@router.get("/settings", response_model=MorningBriefSettingsResponse)
async def get_morning_brief_settings(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MORNING_BRIEF)),
) -> MorningBriefSettingsResponse:
    async with async_session_maker() as session:
        org = await session.get(Organization, current_user.tenant_id)
        return MorningBriefSettingsResponse(
            enabled=org.morning_brief_enabled,
            local_time=org.morning_brief_local_time,
            timezone=org.morning_brief_timezone,
        )


@router.put("/settings", response_model=MorningBriefSettingsResponse)
async def update_morning_brief_settings(
    body: UpdateMorningBriefSettingsRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.GENERATE_MORNING_BRIEF)),
) -> MorningBriefSettingsResponse:
    async with async_session_maker() as session:
        org = await session.get(Organization, current_user.tenant_id)
        org.morning_brief_enabled = body.enabled
        org.morning_brief_local_time = body.local_time
        org.morning_brief_timezone = body.timezone
        await session.commit()
        return MorningBriefSettingsResponse(
            enabled=org.morning_brief_enabled,
            local_time=org.morning_brief_local_time,
            timezone=org.morning_brief_timezone,
        )
