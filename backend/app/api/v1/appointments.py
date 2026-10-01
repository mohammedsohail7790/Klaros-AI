import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Appointment
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _appointment_to_dict(a: Appointment) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "lead_id": str(a.lead_id) if a.lead_id else None,
        "customer_id": str(a.customer_id),
        "assigned_user_id": str(a.assigned_user_id) if a.assigned_user_id else None,
        "title": a.title,
        "service": a.service,
        "location": a.location,
        "start_time": a.start_time.isoformat(),
        "end_time": a.end_time.isoformat(),
        "status": a.status,
        "notes": a.notes,
        # Phase 14: honest sync state for external calendars (Google
        # Calendar, ...) — null/null until a real sync actually sets
        # these (see GoogleCalendarSyncService), never fabricated.
        "external_provider": a.external_provider,
        "external_id": a.external_id,
    }


@router.get("")
async def list_appointments(
    date_from: datetime,
    date_to: datetime,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Read-only listing for the calendar UI (section 21) — a query, not a
    business action, so it reads directly rather than through a tool, same
    pattern as GET /crm/metrics."""
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        rows = (
            await session.execute(
                select(Appointment).where(
                    Appointment.tenant_id == current_user.tenant_id,
                    Appointment.start_time < date_to,
                    Appointment.end_time > date_from,
                ).order_by(Appointment.start_time)
            )
        ).scalars().all()
    return {"appointments": [_appointment_to_dict(a) for a in rows]}


@router.get("/availability")
async def check_availability(
    date_from: datetime,
    date_to: datetime,
    duration_minutes: int = Query(default=60, ge=15, le=480),
    assigned_user_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "duration_minutes": duration_minutes,
        "assigned_user_id": str(assigned_user_id) if assigned_user_id else None,
    }
    try:
        output = await registry.execute("crm.check_availability", payload, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class CreateAppointmentRequest(BaseModel):
    customer_id: uuid.UUID
    title: str
    start_time: datetime
    end_time: datetime
    lead_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    service: str | None = None
    location: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None


@router.post("", status_code=201)
async def create_appointment(
    body: CreateAppointmentRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = body.model_dump(mode="json")
    try:
        output = await registry.execute("crm.create_appointment", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class RescheduleAppointmentRequest(BaseModel):
    start_time: datetime
    end_time: datetime


@router.patch("/{appointment_id}")
async def reschedule_appointment(
    appointment_id: uuid.UUID,
    body: RescheduleAppointmentRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"appointment_id": str(appointment_id), **body.model_dump(mode="json")}
    try:
        output = await registry.execute(
            "crm.reschedule_appointment", payload, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.delete("/{appointment_id}")
async def cancel_appointment(
    appointment_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.cancel_appointment", {"appointment_id": str(appointment_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
