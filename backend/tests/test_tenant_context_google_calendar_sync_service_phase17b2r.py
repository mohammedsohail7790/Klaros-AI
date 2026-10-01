"""Phase 17B-2R: real-PostgreSQL behavioral proof that
GoogleCalendarSyncService's own, independently-opened sessions (4 sites,
including `sync_appointment`'s PostgreSQL-advisory-locked session) now
stamp `SET LOCAL app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations.google_calendar_client import GoogleCalendarClient
from app.integrations.google_calendar_schemas import GoogleCalendar, GoogleEvent
from app.models.crm import Appointment, AppointmentStatus, Customer
from app.models.organization import Organization
from app.services.google_calendar_sync_service import AppointmentNotFoundError, GoogleCalendarSyncService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


@pytest.fixture
def connection_service():
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def _make_org_and_appointment(tenant_id: uuid.UUID) -> uuid.UUID:
    appointment_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="GCal Test Customer"))
        await session.flush()
        start = datetime.now(timezone.utc) + timedelta(days=1)
        session.add(
            Appointment(
                id=appointment_id, tenant_id=tenant_id, customer_id=customer_id, title="Roof inspection",
                start_time=start, end_time=start + timedelta(hours=1), status=AppointmentStatus.CONFIRMED,
            )
        )
        await session.commit()
    return appointment_id


async def _connect_google_calendar(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    async def _fake_get_calendar(self, *, access_token, calendar_id):
        return GoogleCalendar(id="primary", summary="Test Calendar")

    monkeypatch.setattr(GoogleCalendarClient, "get_calendar", _fake_get_calendar)
    await connection_service.connect(
        tenant_id, "google_calendar", {"access_token": "at_valid", "refresh_token": "rt_valid"},
        created_by=None, external_account_id=None, scopes="https://www.googleapis.com/auth/calendar",
    )


@requires_real_postgres
async def test_sync_appointment_sets_tenant_context(monkeypatch, spy, connection_service) -> None:
    import app.services.google_calendar_sync_service as gcs_module

    monkeypatch.setattr(gcs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_id, monkeypatch)
    appointment_id = await _make_org_and_appointment(tenant_id)

    async def _fake_create_event(self, *, access_token, calendar_id, summary, description, location, start_iso, end_iso, time_zone="UTC"):
        return GoogleEvent(id="gevt-phase17b2r", status="confirmed")

    monkeypatch.setattr(GoogleCalendarClient, "create_event", _fake_create_event)

    service = GoogleCalendarSyncService(async_session_maker, connection_service)
    result = await service.sync_appointment(tenant_id, appointment_id)
    assert result.action == "created"

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_sync_tenant_bs_appointment(monkeypatch, connection_service) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _connect_google_calendar(connection_service, tenant_b, monkeypatch)
    appointment_a = await _make_org_and_appointment(tenant_a)

    service = GoogleCalendarSyncService(async_session_maker, connection_service)
    with pytest.raises(AppointmentNotFoundError):
        await service.sync_appointment(tenant_b, appointment_a)
