"""Phase 14: syncs a Klaros `Appointment` to a tenant's own connected
Google Calendar. Deliberately additive, not a replacement for the
internal scheduling engine (`app/calendar/internal_test_adapter.py`
remains the source of truth for availability/double-booking prevention,
same as `QuickBooksSyncService` never replaced internal invoicing) — this
is a one-way push of an already-decided Klaros appointment, plus a
read-only availability check against the tenant's real calendar.

Deliberately a manually-invoked tool (`calendar.sync_appointment_to_
google`), not an automatic event-subscriber on `APPOINTMENT_CREATED`/
`APPOINTMENT_UPDATED` — same reasoning `QuickBooksSyncService` already
established: an external push should be an explicit, auditable action a
human or AI actor took, not a side effect that could silently retry
against a flaky external API on every internal event. The operation
itself IS idempotent regardless (repeated calls converge on the same
Google event, never create duplicates), so nothing is lost by it being
explicit.

Token refresh is handled inline: a 401 on the real API call triggers
exactly one refresh-and-retry, mirroring `QuickBooksSyncService`'s
identical pattern.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text

from app.integrations.credential_store import decrypt_credential
from app.integrations.google_calendar_client import (
    GoogleCalendarAPIError,
    GoogleCalendarClient,
    GoogleCalendarErrorType,
)
from app.integrations.google_calendar_schemas import GoogleCalendarListEntry, GoogleFreeBusyResponse
from app.models.crm import Appointment, AppointmentStatus
from app.models.integration import ConnectionStatus
from app.services.integration_connection_service import IntegrationConnectionService

_PROVIDER = "google_calendar"


class GoogleCalendarNotConnectedError(Exception):
    pass


class AppointmentNotFoundError(Exception):
    pass


@dataclass
class AppointmentSyncResult:
    action: str  # "created" | "updated" | "cancelled" | "already_cancelled"
    google_event_id: str | None


class GoogleCalendarSyncService:
    def __init__(self, session_factory, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def _resolve_credential(self, tenant_id: uuid.UUID) -> tuple[str, str]:
        connection = await self._connection_service.get_connection(tenant_id, _PROVIDER)
        if connection is None or connection.status != ConnectionStatus.CONNECTED or not connection.encrypted_credential:
            raise GoogleCalendarNotConnectedError(
                "Google Calendar is not connected for this tenant — connect it via Settings → Integrations first"
            )
        credential = decrypt_credential(connection.encrypted_credential)
        access_token = credential.get("access_token")
        refresh_token = credential.get("refresh_token")
        if not access_token or not refresh_token:
            raise GoogleCalendarNotConnectedError("Stored Google Calendar credential is missing access_token/refresh_token")
        return access_token, refresh_token

    async def _refresh_and_persist(self, tenant_id: uuid.UUID, refresh_token: str) -> str:
        client = GoogleCalendarClient()
        token_response = await client.refresh_access_token(refresh_token=refresh_token)
        connection = await self._connection_service.get_connection(tenant_id, _PROVIDER)
        await self._connection_service.connect(
            tenant_id, _PROVIDER,
            {
                "access_token": token_response.access_token,
                # Google omits refresh_token on a refresh grant — keep the
                # original one, never overwrite it with None.
                "refresh_token": token_response.refresh_token or refresh_token,
            },
            created_by=connection.created_by if connection else None,
            external_account_id=connection.external_account_id if connection else None,
            scopes=connection.scopes if connection else None,
        )
        return token_response.access_token

    async def list_calendars(self, tenant_id: uuid.UUID) -> list[GoogleCalendarListEntry]:
        access_token, refresh_token = await self._resolve_credential(tenant_id)
        client = GoogleCalendarClient()
        try:
            return await client.list_calendars(access_token=access_token)
        except GoogleCalendarAPIError as exc:
            if exc.error_type == GoogleCalendarErrorType.AUTHENTICATION:
                access_token = await self._refresh_and_persist(tenant_id, refresh_token)
                return await client.list_calendars(access_token=access_token)
            raise

    async def check_availability(
        self, tenant_id: uuid.UUID, *, calendar_id: str, time_min: datetime, time_max: datetime,
    ) -> GoogleFreeBusyResponse:
        access_token, refresh_token = await self._resolve_credential(tenant_id)
        client = GoogleCalendarClient()
        time_min_iso = time_min.astimezone(timezone.utc).isoformat()
        time_max_iso = time_max.astimezone(timezone.utc).isoformat()
        try:
            return await client.query_freebusy(
                access_token=access_token, calendar_id=calendar_id, time_min_iso=time_min_iso, time_max_iso=time_max_iso,
            )
        except GoogleCalendarAPIError as exc:
            if exc.error_type == GoogleCalendarErrorType.AUTHENTICATION:
                access_token = await self._refresh_and_persist(tenant_id, refresh_token)
                return await client.query_freebusy(
                    access_token=access_token, calendar_id=calendar_id, time_min_iso=time_min_iso, time_max_iso=time_max_iso,
                )
            raise

    async def sync_appointment(
        self, tenant_id: uuid.UUID, appointment_id: uuid.UUID, *, calendar_id: str = "primary",
    ) -> AppointmentSyncResult:
        access_token, refresh_token = await self._resolve_credential(tenant_id)
        client = GoogleCalendarClient()

        async def _call(fn, **kwargs):
            nonlocal access_token
            try:
                return await fn(access_token=access_token, **kwargs)
            except GoogleCalendarAPIError as exc:
                if exc.error_type == GoogleCalendarErrorType.AUTHENTICATION:
                    access_token = await self._refresh_and_persist(tenant_id, refresh_token)
                    return await fn(access_token=access_token, **kwargs)
                raise

        # A real PostgreSQL concurrency test proved the previous
        # implementation (read Appointment.external_id in one session,
        # make the real external Google call, write the result back in a
        # second session) was NOT race-safe: two genuinely concurrent
        # syncs of the SAME appointment could both see external_id=NULL,
        # both create a real, distinct Google Calendar event, and the
        # second DB write would silently overwrite the first's
        # external_id — orphaning a real Google event Klaros no longer
        # tracks, with no constraint violation ever raised (external_id
        # is a plain column on the Appointment row, not a separately
        # unique-constrained table). Fixed the same way
        # app/calendar/internal_test_adapter.py already fixed the
        # identical class of problem for appointment double-booking: a
        # PostgreSQL session-level advisory lock, keyed on exactly this
        # appointment, acquired before the read and held for the life of
        # this one session/transaction — including across the external
        # Google API call, a deliberate, narrow exception to "don't hold
        # a DB transaction open across a network call," justified here
        # because this is an explicit, low-frequency, manually-invoked
        # sync action (see the tool's own docstring), not a hot path. A
        # no-op on SQLite (single-writer serialization already prevents
        # this race there, and SQLite has no advisory locks).
        async with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_key = f"google-calendar-sync:{tenant_id}:{appointment_id}"
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})

            appointment = await session.get(Appointment, appointment_id)
            if appointment is None or appointment.tenant_id != tenant_id:
                raise AppointmentNotFoundError("Appointment not found")

            status = appointment.status
            existing_external_id = (
                appointment.external_id if appointment.external_provider == _PROVIDER else None
            )
            summary = appointment.title
            description = "\n".join(filter(None, [appointment.service, appointment.notes])) or None
            location = appointment.location
            start_iso = appointment.start_time.astimezone(timezone.utc).isoformat()
            end_iso = appointment.end_time.astimezone(timezone.utc).isoformat()

            if status == AppointmentStatus.CANCELLED:
                if existing_external_id is None:
                    return AppointmentSyncResult(action="already_cancelled", google_event_id=None)
                try:
                    await _call(client.delete_event, calendar_id=calendar_id, event_id=existing_external_id)
                except GoogleCalendarAPIError as exc:
                    if exc.error_type != GoogleCalendarErrorType.NOT_FOUND:
                        raise
                    # Already gone on Google's side — a safe no-op, not a failure.
                return AppointmentSyncResult(action="cancelled", google_event_id=existing_external_id)

            if existing_external_id is None:
                event = await _call(
                    client.create_event, calendar_id=calendar_id, summary=summary, description=description,
                    location=location, start_iso=start_iso, end_iso=end_iso,
                )
                appointment.external_provider = _PROVIDER
                appointment.external_id = event.id
                await session.commit()
                return AppointmentSyncResult(action="created", google_event_id=event.id)

            event = await _call(
                client.update_event, calendar_id=calendar_id, event_id=existing_external_id, summary=summary,
                description=description, location=location, start_iso=start_iso, end_iso=end_iso,
            )
            return AppointmentSyncResult(action="updated", google_event_id=event.id)
