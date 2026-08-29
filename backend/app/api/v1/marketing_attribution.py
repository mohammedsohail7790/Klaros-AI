from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/attribution", tags=["marketing-attribution"])


@router.post("/leads")
async def attribute_lead(
    body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    """body = {lead_id, campaign_id?, source?, medium?, landing_page?, referral_source?,
    utm_source?, utm_medium?, utm_campaign?, utm_term?, utm_content?, click_id?, attribution_model?}"""
    try:
        output = await registry.execute("marketing.attribute_lead", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
