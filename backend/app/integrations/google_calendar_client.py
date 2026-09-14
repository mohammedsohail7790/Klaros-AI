"""Phase 14: real Google Calendar API v3 client — direct httpx calls (no
`google-api-python-client`/`google-auth` SDK dependency added), matching
this project's established pattern for external HTTP integrations (see
`app/integrations/stripe_client.py`, `app/integrations/quickbooks_client.
py`). Real OAuth2 authorization-code exchange + refresh-token grant, real
error classification, real retry/backoff, real bounded timeout — same
shape as `QuickBooksClient`, applied to a third provider rather than a
third pattern.

Like QuickBooks, Google Calendar has no platform-level shared credential
— every tenant connects their OWN Google Calendar, so this client is
always used through a tenant's own `IntegrationConnection`, never a
platform-wide fallback (`Settings.GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
are the platform OAuth APP's credentials, used to talk to Google's OAuth
server on any tenant's behalf — not a data credential).
"""

from __future__ import annotations

import asyncio
import urllib.parse
from enum import StrEnum
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from app.core.config import get_settings
from app.integrations.google_calendar_schemas import (
    GoogleCalendar,
    GoogleCalendarListResponse,
    GoogleEvent,
    GoogleEventListResponse,
    GoogleFreeBusyResponse,
    GoogleTokenResponse,
)

logger = structlog.get_logger(__name__)

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_API_BASE = "https://www.googleapis.com/calendar/v3"
_SCOPE = "https://www.googleapis.com/auth/calendar"


class GoogleCalendarErrorType(StrEnum):
    """Mirrors `StripeErrorType`/`QuickBooksErrorType`'s categories —
    same reasoning, third provider: lets a caller distinguish an
    invalid/expired token from a transient rate limit from a genuinely
    malformed request."""

    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"


class GoogleCalendarAPIError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None,
        error_type: GoogleCalendarErrorType = GoogleCalendarErrorType.PROVIDER_ERROR,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def _validate_response(model: type[Any], body: dict[str, Any]) -> Any:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise GoogleCalendarAPIError(
            f"Google Calendar returned an unexpected response shape: {exc}",
            error_type=GoogleCalendarErrorType.PROVIDER_ERROR,
        ) from exc


def get_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """Builds the real Google OAuth2 consent-page URL a tenant's browser
    is redirected to. No network call — deterministic URL construction,
    fully testable without any credential. `access_type=offline` +
    `prompt=consent` guarantee a `refresh_token` comes back even if this
    tenant previously granted consent (Google otherwise only issues one
    on the very first consent)."""
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


class GoogleCalendarClient:
    """One real client per call, matching `StripeClient`/`QuickBooksClient`'s
    pattern. Two kinds of calls: OAuth (form-encoded, client_id/
    client_secret in the body per Google's documented token-endpoint
    contract) and Calendar API (Bearer-auth'd with a tenant's own
    access_token)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client_id = settings.GOOGLE_CLIENT_ID
        self._client_secret = settings.GOOGLE_CLIENT_SECRET
        self._timeout_seconds = settings.GOOGLE_CALENDAR_TIMEOUT_SECONDS
        self._max_retries = settings.GOOGLE_CALENDAR_MAX_RETRIES

    def __repr__(self) -> str:
        return "GoogleCalendarClient(is_connected=True)"

    async def _request(
        self, method: str, url: str, *, headers: dict[str, str], data: Any = None, json_body: dict | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.request(
                        method, url, headers=headers, data=data, json=json_body, params=params
                    )
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("google_calendar_request_attempt_failed", attempt=attempt, error_type="timeout", retryable=True)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise GoogleCalendarAPIError(
                    f"Google Calendar request timed out after {self._max_retries} attempts",
                    error_type=GoogleCalendarErrorType.TIMEOUT,
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("google_calendar_request_attempt_failed", attempt=attempt, error_type="network_error", retryable=True)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise GoogleCalendarAPIError(
                    f"Google Calendar request failed: {exc}", error_type=GoogleCalendarErrorType.NETWORK_ERROR
                ) from exc

            if response.status_code in (401, 403):
                message = _extract_error_message(response)
                raise GoogleCalendarAPIError(message, status_code=response.status_code, error_type=GoogleCalendarErrorType.AUTHENTICATION)

            if response.status_code == 404:
                message = _extract_error_message(response)
                raise GoogleCalendarAPIError(message, status_code=404, error_type=GoogleCalendarErrorType.NOT_FOUND)

            if response.status_code == 429 or response.status_code >= 500:
                error_type = GoogleCalendarErrorType.RATE_LIMIT if response.status_code == 429 else GoogleCalendarErrorType.PROVIDER_ERROR
                logger.warning(
                    "google_calendar_request_attempt_failed", attempt=attempt, error_type=error_type.value,
                    status_code=response.status_code, retryable=True,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                message = _extract_error_message(response)
                raise GoogleCalendarAPIError(message, status_code=response.status_code, error_type=error_type)

            if response.status_code >= 400:
                message = _extract_error_message(response)
                raise GoogleCalendarAPIError(message, status_code=response.status_code, error_type=GoogleCalendarErrorType.INVALID_REQUEST)

            if response.status_code == 204 or not response.content:
                return {}
            return response.json()

        raise GoogleCalendarAPIError(
            f"Google Calendar request failed after retries: {last_exc}", error_type=GoogleCalendarErrorType.NETWORK_ERROR
        )

    async def exchange_code_for_tokens(self, *, code: str, redirect_uri: str) -> GoogleTokenResponse:
        if not self._client_id or not self._client_secret:
            raise GoogleCalendarAPIError(
                "Google OAuth app is not configured (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET unset)",
                error_type=GoogleCalendarErrorType.INVALID_REQUEST,
            )
        body = await self._request(
            "POST", _OAUTH_TOKEN_URL, headers={"Accept": "application/json"},
            data={
                "client_id": self._client_id, "client_secret": self._client_secret,
                "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            },
        )
        return _validate_response(GoogleTokenResponse, body)

    async def refresh_access_token(self, *, refresh_token: str) -> GoogleTokenResponse:
        if not self._client_id or not self._client_secret:
            raise GoogleCalendarAPIError(
                "Google OAuth app is not configured (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET unset)",
                error_type=GoogleCalendarErrorType.INVALID_REQUEST,
            )
        body = await self._request(
            "POST", _OAUTH_TOKEN_URL, headers={"Accept": "application/json"},
            data={
                "client_id": self._client_id, "client_secret": self._client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token",
            },
        )
        return _validate_response(GoogleTokenResponse, body)

    async def list_calendars(self, *, access_token: str) -> list[Any]:
        body = await self._request(
            "GET", f"{_API_BASE}/users/me/calendarList", headers={"Authorization": f"Bearer {access_token}"},
        )
        return _validate_response(GoogleCalendarListResponse, body).items

    async def get_calendar(self, *, access_token: str, calendar_id: str = "primary") -> GoogleCalendar:
        """A real, cheap, read-only call — used as the connection
        verifier (mirrors `StripeClient.verify_connection`/
        `QuickBooksClient.get_company_info`)."""
        body = await self._request(
            "GET", f"{_API_BASE}/calendars/{calendar_id}", headers={"Authorization": f"Bearer {access_token}"},
        )
        return _validate_response(GoogleCalendar, body)

    async def create_event(
        self, *, access_token: str, calendar_id: str, summary: str, description: str | None,
        location: str | None, start_iso: str, end_iso: str, time_zone: str = "UTC",
    ) -> GoogleEvent:
        payload = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start_iso, "timeZone": time_zone},
            "end": {"dateTime": end_iso, "timeZone": time_zone},
        }
        body = await self._request(
            "POST", f"{_API_BASE}/calendars/{calendar_id}/events",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json_body=payload,
        )
        return _validate_response(GoogleEvent, body)

    async def list_events(
        self, *, access_token: str, calendar_id: str, time_min_iso: str, time_max_iso: str,
        max_results: int = 100, page_token: str | None = None,
    ) -> GoogleEventListResponse:
        """The pull/import direction's read — every other method on this
        client either pushes an event Klaros itself created or reads one
        specific already-known event. `singleEvents=true` expands
        recurring events into individual instances (Google's own
        documented way to get concrete start/end times rather than a
        recurrence rule this app has no use for)."""
        params = {
            "timeMin": time_min_iso, "timeMax": time_max_iso, "maxResults": str(max_results),
            "singleEvents": "true", "orderBy": "startTime",
        }
        if page_token:
            params["pageToken"] = page_token
        query = urllib.parse.urlencode(params)
        body = await self._request(
            "GET", f"{_API_BASE}/calendars/{calendar_id}/events?{query}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return _validate_response(GoogleEventListResponse, body)

    async def get_event(self, *, access_token: str, calendar_id: str, event_id: str) -> GoogleEvent:
        body = await self._request(
            "GET", f"{_API_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return _validate_response(GoogleEvent, body)

    async def update_event(
        self, *, access_token: str, calendar_id: str, event_id: str, summary: str, description: str | None,
        location: str | None, start_iso: str, end_iso: str, time_zone: str = "UTC",
    ) -> GoogleEvent:
        payload = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start_iso, "timeZone": time_zone},
            "end": {"dateTime": end_iso, "timeZone": time_zone},
        }
        body = await self._request(
            "PUT", f"{_API_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json_body=payload,
        )
        return _validate_response(GoogleEvent, body)

    async def delete_event(self, *, access_token: str, calendar_id: str, event_id: str) -> None:
        """Google's `DELETE` returns 204 with no body on success, and a
        404 if the event was already deleted — the caller (sync service)
        treats a 404 here as a benign already-gone case, not a failure."""
        await self._request(
            "DELETE", f"{_API_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def query_freebusy(
        self, *, access_token: str, calendar_id: str, time_min_iso: str, time_max_iso: str,
    ) -> GoogleFreeBusyResponse:
        payload = {"timeMin": time_min_iso, "timeMax": time_max_iso, "items": [{"id": calendar_id}]}
        body = await self._request(
            "POST", f"{_API_BASE}/freeBusy",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json_body=payload,
        )
        return _validate_response(GoogleFreeBusyResponse, body)


def _extract_error_message(response: httpx.Response) -> str:
    try:
        body = response.json() if response.content else {}
    except ValueError:
        return response.text
    error = body.get("error")
    if isinstance(error, dict):
        return error.get("message", response.text)
    if isinstance(error, str):
        # Google's OAuth token endpoint uses a flatter {"error": "...",
        # "error_description": "..."} shape, distinct from the Calendar
        # API's {"error": {"message": ...}} — handle both.
        return body.get("error_description", error)
    return response.text
