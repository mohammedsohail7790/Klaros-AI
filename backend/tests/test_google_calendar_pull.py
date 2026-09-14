"""The Google Calendar PULL direction (calendar.import_from_google /
GoogleCalendarSyncService.import_events) — the counterpart to
test_google_calendar_integration.py's push-direction tests. Mocks
GoogleCalendarClient.list_events directly, same pattern the push tests
use for create_event/update_event — no real Google credentials needed.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.google_calendar_client import GoogleCalendarAPIError, GoogleCalendarClient, GoogleCalendarErrorType
from app.integrations.google_calendar_schemas import (
    GoogleEvent,
    GoogleEventAttendee,
    GoogleEventDateTime,
    GoogleEventListResponse,
)
from app.models.crm import Appointment, AppointmentStatus, Customer
from app.services.google_calendar_sync_service import GoogleCalendarNotConnectedError, GoogleCalendarSyncService
from app.services.integration_connection_service import IntegrationConnectionService

pytestmark = pytest.mark.asyncio

_NOW = datetime.now(timezone.utc)


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def _connect_google_calendar(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    from app.integrations.google_calendar_schemas import GoogleCalendar

    async def _fake_get_calendar(self, *, access_token, calendar_id):
        return GoogleCalendar(id="primary", summary="Acme Co Calendar")

    monkeypatch.setattr(GoogleCalendarClient, "get_calendar", _fake_get_calendar)
    await connection_service.connect(
        tenant_id, "google_calendar", {"access_token": "at_valid", "refresh_token": "rt_valid"},
        created_by=None, external_account_id=None, scopes="https://www.googleapis.com/auth/calendar",
    )


def _sync_service(connection_service) -> GoogleCalendarSyncService:
    from app.db.session import async_session_maker

    return GoogleCalendarSyncService(async_session_maker, connection_service)


def _dt(offset_hours: float) -> GoogleEventDateTime:
    return GoogleEventDateTime(dateTime=(_NOW + timedelta(hours=offset_hours)).isoformat())


def _paged_events(items):
    async def _fake(self, *, access_token, calendar_id, time_min_iso, time_max_iso, max_results, page_token):
        if page_token:
            return GoogleEventListResponse(items=[], nextPageToken=None)
        return GoogleEventListResponse(items=items, nextPageToken=None)

    return _fake


async def test_no_connection_raises(connection_service) -> None:
    service = _sync_service(connection_service)
    with pytest.raises(GoogleCalendarNotConnectedError):
        await service.import_events(uuid.uuid4(), time_min=_NOW, time_max=_NOW + timedelta(days=7))


async def test_event_with_attendee_creates_customer_and_appointment(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    event = GoogleEvent(
        id="gcal-evt-1", status="confirmed", summary="Site visit",
        start=_dt(2), end=_dt(3),
        attendees=[GoogleEventAttendee(email="realcustomer@example.com", displayName="Real Customer")],
    )
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    result = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))
    assert result.appointments_created == 1
    assert result.appointments_skipped == 0

    from app.db.session import async_session_maker
    from sqlalchemy import select

    async with async_session_maker() as session:
        appointment = (
            await session.execute(select(Appointment).where(Appointment.tenant_id == tenant_id))
        ).scalar_one()
        customer = await session.get(Customer, appointment.customer_id)

    assert appointment.title == "Site visit"
    assert appointment.external_provider == "google_calendar"
    assert appointment.external_id == "gcal-evt-1"
    assert appointment.status == AppointmentStatus.CONFIRMED
    assert customer.email == "realcustomer@example.com"


async def test_past_event_imports_as_completed(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    event = GoogleEvent(
        id="gcal-evt-2", status="confirmed", summary="Past job",
        start=GoogleEventDateTime(dateTime=(_NOW - timedelta(days=5)).isoformat()),
        end=GoogleEventDateTime(dateTime=(_NOW - timedelta(days=5, hours=-1)).isoformat()),
        attendees=[GoogleEventAttendee(email="pastcustomer@example.com")],
    )
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    result = await service.import_events(tenant_id, time_min=_NOW - timedelta(days=10), time_max=_NOW)

    from app.db.session import async_session_maker
    from sqlalchemy import select

    async with async_session_maker() as session:
        appointment = (
            await session.execute(select(Appointment).where(Appointment.tenant_id == tenant_id))
        ).scalar_one()
    assert appointment.status == AppointmentStatus.COMPLETED
    assert result.appointments_created == 1


async def test_matches_existing_customer_instead_of_duplicating(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        existing = Customer(tenant_id=tenant_id, name="Already Here", email="already@example.com")
        session.add(existing)
        await session.commit()
        await session.refresh(existing)

    event = GoogleEvent(
        id="gcal-evt-3", status="confirmed", summary="Follow-up",
        start=_dt(4), end=_dt(5),
        attendees=[GoogleEventAttendee(email="ALREADY@example.com")],
    )
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))

    from sqlalchemy import select

    async with async_session_maker() as session:
        customers = (
            await session.execute(select(Customer).where(Customer.tenant_id == tenant_id))
        ).scalars().all()
    assert len(customers) == 1  # no duplicate customer created


async def test_all_day_event_is_skipped(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    event = GoogleEvent(
        id="gcal-evt-4", status="confirmed", summary="Company Holiday",
        start=GoogleEventDateTime(date="2026-12-25"), end=GoogleEventDateTime(date="2026-12-26"),
    )
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    result = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=30))
    assert result.appointments_created == 0
    assert result.appointments_skipped == 1
    assert "start/end" in result.results[0].reason


async def test_event_with_no_attendee_or_matchable_name_is_skipped(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    event = GoogleEvent(id="gcal-evt-5", status="confirmed", summary="Lunch", start=_dt(1), end=_dt(2))
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    result = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))
    assert result.appointments_created == 0
    assert result.appointments_skipped == 1


async def test_repeat_import_skips_already_linked_event(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    event = GoogleEvent(
        id="gcal-evt-6", status="confirmed", summary="Repeat test", start=_dt(2), end=_dt(3),
        attendees=[GoogleEventAttendee(email="repeat@example.com")],
    )
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    first = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))
    second = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))
    assert first.appointments_created == 1
    assert second.appointments_created == 0
    assert second.appointments_skipped == 0  # already-linked events aren't even reported


async def test_cancelled_event_is_never_imported(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    event = GoogleEvent(
        id="gcal-evt-7", status="cancelled", summary="Cancelled meeting", start=_dt(2), end=_dt(3),
        attendees=[GoogleEventAttendee(email="cancelled@example.com")],
    )
    monkeypatch.setattr(GoogleCalendarClient, "list_events", _paged_events([event]))

    service = _sync_service(connection_service)
    result = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))
    assert result.appointments_created == 0
    assert result.appointments_skipped == 0


async def test_401_during_import_refreshes_token_once_and_retries(connection_service, monkeypatch) -> None:
    from app.integrations.google_calendar_schemas import GoogleTokenResponse

    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)

    calls = {"n": 0}

    async def _fake_list_events(self, *, access_token, calendar_id, time_min_iso, time_max_iso, max_results, page_token):
        calls["n"] += 1
        if calls["n"] == 1:
            raise GoogleCalendarAPIError("Token expired", error_type=GoogleCalendarErrorType.AUTHENTICATION)
        assert access_token == "at_refreshed"
        return GoogleEventListResponse(items=[], nextPageToken=None)

    async def _fake_refresh(self, *, refresh_token):
        return GoogleTokenResponse(access_token="at_refreshed", refresh_token="rt_refreshed", expires_in=3600)

    monkeypatch.setattr(GoogleCalendarClient, "list_events", _fake_list_events)
    monkeypatch.setattr(GoogleCalendarClient, "refresh_access_token", _fake_refresh)

    service = _sync_service(connection_service)
    result = await service.import_events(tenant_id, time_min=_NOW, time_max=_NOW + timedelta(days=7))
    assert result.appointments_created == 0
    assert calls["n"] == 2
