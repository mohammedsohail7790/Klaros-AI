"""Phase 17B-2R: real-PostgreSQL behavioral proof that CompletionService's
own, independently-opened sessions (2 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.operations import Job, JobStatus
from app.models.organization import Organization
from app.services.completion_service import CloseOutNotReadyError, CompletionService, JobNotFoundError

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
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Completion Test Customer"))
        session.add(
            Job(
                id=job_id, tenant_id=tenant_id, customer_id=customer_id, job_number=f"J-{uuid.uuid4().hex[:6]}",
                title="Completion Test Job", status=JobStatus.COMPLETED,
            )
        )
        await session.commit()
    return job_id


def _service() -> CompletionService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return CompletionService(async_session_maker, bus)


@requires_real_postgres
async def test_generate_completion_packet_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.completion_service as completion_service_module

    monkeypatch.setattr(completion_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    job_id = await _make_org_and_job(tenant_id)
    service = _service()

    packet = await service.generate_completion_packet(tenant_id, job_id)
    assert packet.job_id == job_id

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_generate_packet_for_tenant_bs_job() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_org_and_job(tenant_a)
    await _make_org_and_job(tenant_b)
    service = _service()

    with pytest.raises(JobNotFoundError):
        await service.generate_completion_packet(tenant_b, job_a)


@requires_real_postgres
async def test_close_job_without_readiness_raises_and_still_isolates_tenants() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_org_and_job(tenant_a)
    await _make_org_and_job(tenant_b)
    service = _service()

    with pytest.raises(JobNotFoundError):
        await service.close_job(tenant_b, job_a)

    with pytest.raises(CloseOutNotReadyError):
        await service.close_job(tenant_a, job_a)
