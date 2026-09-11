"""Phase 14: Google Calendar tools — exposed through the same
ToolRegistry -> permission -> tenant -> policy -> execution -> audit
pipeline as every other tool, no provider-specific shortcut. Reuses
existing permissions (`READ_APPOINTMENTS`, `CREATE_APPOINTMENT`,
`MANAGE_INTEGRATIONS`) rather than inventing new ones — none of these
operations need a permission tier that doesn't already exist.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.integrations.google_calendar_client import GoogleCalendarAPIError
from app.models.rbac import Permission
from app.services.google_calendar_sync_service import (
    AppointmentNotFoundError,
    GoogleCalendarNotConnectedError,
    GoogleCalendarSyncService,
)
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import ToolError


class ListGoogleCalendarsInput(BaseModel):
    pass


class ListGoogleCalendarsOutput(BaseModel):
    calendars: list[dict[str, Any]]


class ListGoogleCalendars(Tool):
    name = "calendar.list_google_calendars"
    description = "List the calendars in this tenant's connected Google Calendar account."
    input_schema = ListGoogleCalendarsInput
    output_schema = ListGoogleCalendarsOutput
    required_permission = Permission.MANAGE_INTEGRATIONS

    def __init__(self, sync_service: GoogleCalendarSyncService) -> None:
        self._sync_service = sync_service

    async def execute(self, input: ListGoogleCalendarsInput, context: ExecutionContext) -> ListGoogleCalendarsOutput:
        try:
            calendars = await self._sync_service.list_calendars(context.tenant_id)
        except GoogleCalendarNotConnectedError as exc:
            raise ToolError(str(exc)) from exc
        except GoogleCalendarAPIError as exc:
            raise ToolError(f"Google Calendar request failed: {exc}") from exc
        return ListGoogleCalendarsOutput(
            calendars=[
                {"id": c.id, "summary": c.summary, "primary": c.primary, "time_zone": c.timeZone} for c in calendars
            ]
        )


class CheckGoogleAvailabilityInput(BaseModel):
    calendar_id: str = "primary"
    time_min: datetime
    time_max: datetime


class CheckGoogleAvailabilityOutput(BaseModel):
    busy: list[dict[str, str]]


class CheckGoogleAvailability(Tool):
    name = "calendar.check_google_availability"
    description = "Query free/busy intervals on a tenant's connected Google Calendar for a time range."
    input_schema = CheckGoogleAvailabilityInput
    output_schema = CheckGoogleAvailabilityOutput
    required_permission = Permission.READ_APPOINTMENTS

    def __init__(self, sync_service: GoogleCalendarSyncService) -> None:
        self._sync_service = sync_service

    async def execute(self, input: CheckGoogleAvailabilityInput, context: ExecutionContext) -> CheckGoogleAvailabilityOutput:
        try:
            result = await self._sync_service.check_availability(
                context.tenant_id, calendar_id=input.calendar_id, time_min=input.time_min, time_max=input.time_max,
            )
        except GoogleCalendarNotConnectedError as exc:
            raise ToolError(str(exc)) from exc
        except GoogleCalendarAPIError as exc:
            raise ToolError(f"Google Calendar request failed: {exc}") from exc
        entry = result.calendars.get(input.calendar_id)
        busy = entry.busy if entry else []
        return CheckGoogleAvailabilityOutput(
            busy=[{"start": str(b.get("start", "")), "end": str(b.get("end", ""))} for b in busy]
        )


class SyncAppointmentToGoogleInput(BaseModel):
    appointment_id: uuid.UUID
    calendar_id: str = "primary"


class SyncAppointmentToGoogleOutput(BaseModel):
    action: str
    google_event_id: str | None


class SyncAppointmentToGoogle(Tool):
    """Idempotent by construction (see `GoogleCalendarSyncService.
    sync_appointment`): creates the Google event the first time,
    updates it on subsequent calls, deletes/cancels it once the
    appointment itself is CANCELLED — never creates a duplicate."""

    name = "calendar.sync_appointment_to_google"
    description = "Push a Klaros appointment to the tenant's connected Google Calendar (create, update, or cancel as appropriate)."
    input_schema = SyncAppointmentToGoogleInput
    output_schema = SyncAppointmentToGoogleOutput
    required_permission = Permission.CREATE_APPOINTMENT

    def __init__(self, sync_service: GoogleCalendarSyncService) -> None:
        self._sync_service = sync_service

    async def execute(self, input: SyncAppointmentToGoogleInput, context: ExecutionContext) -> SyncAppointmentToGoogleOutput:
        try:
            result = await self._sync_service.sync_appointment(
                context.tenant_id, input.appointment_id, calendar_id=input.calendar_id,
            )
        except (GoogleCalendarNotConnectedError, AppointmentNotFoundError) as exc:
            raise ToolError(str(exc)) from exc
        except GoogleCalendarAPIError as exc:
            raise ToolError(f"Google Calendar sync failed: {exc}") from exc
        return SyncAppointmentToGoogleOutput(action=result.action, google_event_id=result.google_event_id)
