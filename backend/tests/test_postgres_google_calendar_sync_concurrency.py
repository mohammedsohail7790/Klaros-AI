"""Real-PostgreSQL concurrency verification for
`GoogleCalendarSyncService.sync_appointment`
(app/services/google_calendar_sync_service.py). The tool's own docstring
(app/tools/builtin/google_calendar_tools.py::SyncAppointmentToGoogle)
claims this operation "never creates a duplicate" — true under
sequential calls (existing tests), but the pre-fix implementation reads
`Appointment.external_id` in one session, makes a real external Google
API call, then writes the result back in a second session — a classic
check-then-act race with no lock held across the external call. Because
`external_id` lives as a plain column on the Appointment row itself
(not a separately unique-constrained table), two genuinely concurrent
syncs of the SAME appointment would both see `external_id IS NULL`, both
create a real (distinct) Google Calendar event, and the second DB write
would silently overwrite the first's `external_id` — orphaning one real
Google event Klaros no longer tracks, never raising any error. This
mirrors exactly the class of gap the Phase 28-30 methodology found real
defects in for Contract/Quote/AR sweeps, and the fix mirrors the
`pg_advisory_xact_lock` pattern already established in
app/calendar/internal_test_adapter.py for calendar double-booking
prevention — this file exists to observe real behavior under genuine
concurrent PostgreSQL transactions, not to assume either safety or a
defect.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.integrations.google_calendar_client import GoogleCalendarClient
from app.integrations.google_calendar_schemas import GoogleCalendar, GoogleEvent
from app.models.crm import Appointment, AppointmentStatus, Customer
from app.services.google_calendar_sync_service import GoogleCalendarSyncService
from app.services.integration_connection_service import IntegrationConnectionService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


async def _make_appointment(tenant_id: uuid.UUID) -> Appointment:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="GCal Concurrency Test Customer")
        session.add(customer)
        await session.flush()
        start = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)
        appointment = Appointment(
            tenant_id=tenant_id, customer_id=customer.id, title="Concurrency Test Appointment",
            start_time=start, end_time=start + timedelta(hours=1), status=AppointmentStatus.CONFIRMED,
        )
        session.add(appointment)
        await session.commit()
        await session.refresh(appointment)
        return appointment


async def _connect_google_calendar(connection_service: IntegrationConnectionService, tenant_id: uuid.UUID, monkeypatch) -> None:
    async def _fake_get_calendar(self, *, access_token, calendar_id):
        return GoogleCalendar(id="primary", summary="Concurrency Test Calendar")

    monkeypatch.setattr(GoogleCalendarClient, "get_calendar", _fake_get_calendar)
    await connection_service.connect(
        tenant_id, "google_calendar", {"access_token": "at_valid", "refresh_token": "rt_valid"},
        created_by=None, external_account_id=None, scopes="https://www.googleapis.com/auth/calendar",
    )


@requires_real_postgres
async def test_genuinely_concurrent_sync_of_the_same_appointment_creates_exactly_one_google_event(monkeypatch) -> None:
    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.db.session import async_session_maker

    connection_service = get_integration_connection_service()
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)
    appointment = await _make_appointment(tenant_id)

    create_calls: list[str] = []
    update_calls: list[str] = []

    async def _fake_create_event(self, *, access_token, calendar_id, summary, description, location, start_iso, end_iso, time_zone="UTC"):
        # A real external API call is never instantaneous — a small,
        # deliberate delay widens the race window so this test doesn't
        # depend on 10 coroutines happening to interleave within a
        # microsecond, mirroring the Phase 29 "artificial delay to
        # reliably reproduce" methodology.
        await asyncio.sleep(0.05)
        event_id = f"gevt-{len(create_calls)}"
        create_calls.append(event_id)
        return GoogleEvent(id=event_id, status="confirmed")

    async def _fake_update_event(self, *, access_token, calendar_id, event_id, summary, description, location, start_iso, end_iso, time_zone="UTC"):
        update_calls.append(event_id)
        return GoogleEvent(id=event_id, status="confirmed")

    monkeypatch.setattr(GoogleCalendarClient, "create_event", _fake_create_event)
    monkeypatch.setattr(GoogleCalendarClient, "update_event", _fake_update_event)

    service = GoogleCalendarSyncService(async_session_maker, connection_service)

    # 10 genuinely concurrent syncs of the SAME appointment.
    results = await asyncio.gather(
        *[service.sync_appointment(tenant_id, appointment.id) for _ in range(10)]
    )

    assert len(create_calls) == 1, f"expected exactly 1 real Google create_event call, got {len(create_calls)}: {create_calls}"
    created = [r for r in results if r.action == "created"]
    updated = [r for r in results if r.action == "updated"]
    assert len(created) == 1, f"expected exactly 1 'created' result, got {len(created)}"
    assert len(updated) == 9, f"expected exactly 9 'updated' results, got {len(updated)}"
    # Every result (created + updated) must reference the SAME single
    # real Google event — never a second, orphaned one.
    assert {r.google_event_id for r in results} == {create_calls[0]}

    async with async_session_maker() as session:
        refreshed = await session.get(Appointment, appointment.id)
        assert refreshed.external_provider == "google_calendar"
        assert refreshed.external_id == create_calls[0]
