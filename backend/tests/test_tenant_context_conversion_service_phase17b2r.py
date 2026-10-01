"""Phase 17B-2R: real-PostgreSQL behavioral proof that LeadConversionService's
own, independently-opened session (1 site) now stamps `SET LOCAL
app.tenant_id`.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Lead
from app.models.organization import Organization
from app.services.conversion_service import LeadConversionService, LeadNotFoundError
from app.services.job_service import JobService

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


async def _make_org_and_lead(tenant_id: uuid.UUID) -> uuid.UUID:
    lead_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Lead(id=lead_id, tenant_id=tenant_id, name="Conversion Test Lead", source="web"))
        await session.commit()
    return lead_id


def _service() -> LeadConversionService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    calendar = InternalTestCalendarAdapter(async_session_maker)
    job_service = JobService(async_session_maker, bus)
    return LeadConversionService(async_session_maker, bus, calendar, job_service)


@requires_real_postgres
async def test_convert_and_book_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.conversion_service as conversion_service_module

    monkeypatch.setattr(conversion_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    lead_id = await _make_org_and_lead(tenant_id)
    service = _service()

    start = datetime.now(timezone.utc) + timedelta(days=1)
    result = await service.convert_and_book(
        tenant_id, lead_id, title="Consult", start_time=start, end_time=start + timedelta(hours=1),
    )
    assert result.job.tenant_id == tenant_id

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_convert_tenant_bs_lead() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_a = await _make_org_and_lead(tenant_a)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()
    service = _service()

    start = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(LeadNotFoundError):
        await service.convert_and_book(
            tenant_b, lead_a, title="cross-tenant probe", start_time=start, end_time=start + timedelta(hours=1),
        )
