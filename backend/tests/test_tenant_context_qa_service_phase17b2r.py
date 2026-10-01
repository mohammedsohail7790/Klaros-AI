"""Phase 17B-2R: real-PostgreSQL behavioral proof that QAService's own,
independently-opened sessions (3 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.operations import Job, JobStatus
from app.models.organization import Organization
from app.services.qa_service import JobNotFoundError, QAService

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


async def _make_org_and_job(tenant_id: uuid.UUID) -> uuid.UUID:
    job_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="QA Test Customer"))
        session.add(
            Job(
                id=job_id, tenant_id=tenant_id, customer_id=customer_id, job_number=f"J-{uuid.uuid4().hex[:6]}",
                title="QA Test Job", status=JobStatus.QA_PENDING, actual_start=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    return job_id


def _service() -> QAService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return QAService(async_session_maker, bus)


@requires_real_postgres
async def test_start_qa_and_fail_qa_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.qa_service as qa_service_module

    monkeypatch.setattr(qa_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    job_id = await _make_org_and_job(tenant_id)
    service = _service()

    await service.start_qa(tenant_id, job_id)
    await service.fail_qa(tenant_id, job_id, reason="Missing documentation")

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_start_qa_on_tenant_bs_job() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_org_and_job(tenant_a)
    await _make_org_and_job(tenant_b)
    service = _service()

    with pytest.raises(JobNotFoundError):
        await service.start_qa(tenant_b, job_a)
