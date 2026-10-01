"""Phase 17B-2R: real-PostgreSQL behavioral proof that DelayDetectionService's
own, independently-opened session (1 site) now stamps `SET LOCAL
app.tenant_id`.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.operations import Job, JobStatus
from app.models.organization import Organization
from app.services.delay_detection_service import DelayDetectionService
from app.services.exception_service import ExceptionService

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


async def _make_org_customer_and_job(tenant_id: uuid.UUID) -> None:
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Delay Test Customer"))
        session.add(
            Job(
                tenant_id=tenant_id, customer_id=customer_id, job_number=f"J-{uuid.uuid4().hex[:6]}",
                title="Delayed Job", status=JobStatus.IN_PROGRESS,
                scheduled_end=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        await session.commit()


def _service() -> DelayDetectionService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return DelayDetectionService(async_session_maker, bus, ExceptionService(async_session_maker, bus))


@requires_real_postgres
async def test_run_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.delay_detection_service as dds_module

    monkeypatch.setattr(dds_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org_customer_and_job(tenant_id)
    service = _service()

    result = await service.run(tenant_id)
    assert result["JOB_DELAYED"] == 1

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_delayed_job_never_flagged_for_tenant_b() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org_customer_and_job(tenant_a)
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_b, name=f"org-{tenant_b}", slug=f"org-{tenant_b}"))
        await session.commit()

    service = _service()
    result_b = await service.run(tenant_b)
    assert result_b == {"JOB_DELAYED": 0, "JOB_OVERDUE": 0, "JOB_UNASSIGNED": 0}
