import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/calendar/google", tags=["google-calendar"])


async def _call_tool(tool_name: str, payload: dict, current_user: CurrentUser, registry: ToolRegistry) -> dict:
    try:
        output = await registry.execute(tool_name, payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/calendars")
async def list_google_calendars(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool("calendar.list_google_calendars", {}, current_user, registry)


@router.get("/availability")
async def check_google_availability(
    calendar_id: str,
    time_min: str,
    time_max: str,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "calendar.check_google_availability",
        {"calendar_id": calendar_id, "time_min": time_min, "time_max": time_max},
        current_user, registry,
    )


@router.post("/appointments/{appointment_id}/sync")
async def sync_appointment_to_google(
    appointment_id: uuid.UUID,
    calendar_id: str = "primary",
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "calendar.sync_appointment_to_google",
        {"appointment_id": str(appointment_id), "calendar_id": calendar_id},
        current_user, registry,
    )


class ImportFromGoogleRequest(BaseModel):
    calendar_id: str = "primary"
    time_min: str
    time_max: str
    max_records: int = 300


@router.post("/import")
async def import_from_google_calendar(
    body: ImportFromGoogleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _call_tool(
        "calendar.import_from_google",
        {
            "calendar_id": body.calendar_id, "time_min": body.time_min, "time_max": body.time_max,
            "max_records": body.max_records,
        },
        current_user, registry,
    )
