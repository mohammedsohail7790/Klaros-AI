"""Phase 14: Pydantic schemas for the Google Calendar API v3 objects this
application actually reads or writes — not a reproduction of Google's
full API surface. Every model uses `extra="allow"` so a field Google adds
later is preserved, never rejected, matching the pattern established for
Stripe (`stripe_schemas.py`) and QuickBooks (`quickbooks_schemas.py`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GoogleTokenResponse(BaseModel):
    """Response from Google's OAuth2 token endpoint
    (`https://oauth2.googleapis.com/token`), for both the initial
    authorization-code exchange and a refresh-token grant. Google omits
    `refresh_token` from a refresh-grant response (the original one stays
    valid) — callers must not assume it's always present."""

    model_config = ConfigDict(extra="allow")

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int
    scope: str | None = None


class GoogleCalendarListEntry(BaseModel):
    """One entry from `GET /users/me/calendarList` — the subset this app
    reads when listing a tenant's calendars."""

    model_config = ConfigDict(extra="allow")

    id: str
    summary: str | None = None
    primary: bool = False
    accessRole: str | None = None
    timeZone: str | None = None


class GoogleCalendarListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[GoogleCalendarListEntry] = Field(default_factory=list)


class GoogleCalendar(BaseModel):
    """`GET /calendars/{calendarId}` — used only as a real, cheap,
    read-only verification call (mirrors `QuickBooksClient.
    get_company_info`/`StripeClient.verify_connection`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    summary: str | None = None
    timeZone: str | None = None


class GoogleEventDateTime(BaseModel):
    """Google represents both timed and all-day events with this same
    shape (`dateTime` for timed, `date` for all-day) — this app only ever
    creates timed events, but tolerates either on read."""

    model_config = ConfigDict(extra="allow")

    dateTime: str | None = None
    date: str | None = None
    timeZone: str | None = None


class GoogleEventAttendee(BaseModel):
    model_config = ConfigDict(extra="allow")

    email: str | None = None
    displayName: str | None = None
    organizer: bool = False
    self_: bool = Field(default=False, alias="self")


class GoogleEvent(BaseModel):
    """The subset of a Calendar `Event` resource this app reads/writes —
    `id` is what gets stored on `Appointment.external_id`, `status`
    reflects Google's own cancellation state (`"cancelled"` if deleted
    via a sync rather than a hard delete, depending on the call used).
    `attendees` is only populated on read (the pull/import direction) —
    this app never sets attendees when creating an event."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    status: str | None = None
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: GoogleEventDateTime | None = None
    end: GoogleEventDateTime | None = None
    htmlLink: str | None = None
    attendees: list[GoogleEventAttendee] = Field(default_factory=list)


class GoogleEventListResponse(BaseModel):
    """`GET /calendars/{id}/events` — the pull/import direction's list
    response. `nextPageToken` present means there are more events past
    this page."""

    model_config = ConfigDict(extra="allow")

    items: list[GoogleEvent] = Field(default_factory=list)
    nextPageToken: str | None = None


class GoogleFreeBusyCalendarEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    busy: list[dict[str, Any]] = Field(default_factory=list)


class GoogleFreeBusyResponse(BaseModel):
    """`POST /freeBusy` — keyed by calendar id; this app reads the
    `busy` intervals for the one calendar it queried."""

    model_config = ConfigDict(extra="allow")

    calendars: dict[str, GoogleFreeBusyCalendarEntry] = Field(default_factory=dict)


class GoogleErrorDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str | None = None
    reason: str | None = None


class GoogleErrorBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int | None = None
    message: str | None = None
    errors: list[GoogleErrorDetail] = Field(default_factory=list)


class GoogleErrorResponse(BaseModel):
    """Google wraps API errors as `{"error": {"code": ..., "message":
    ..., "errors": [...]}}` — a distinct shape from Stripe's `{"error":
    {...}}`/QuickBooks' `{"Fault": {"Error": [...]}}`, handled by its own
    schema rather than forcing a shared one."""

    model_config = ConfigDict(extra="allow")

    error: GoogleErrorBody | None = None
