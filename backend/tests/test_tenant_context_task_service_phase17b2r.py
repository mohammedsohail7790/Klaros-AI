"""Phase 17B-2R: real-PostgreSQL behavioral proof that TaskService's own,
independently-opened sessions (2 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Customer
from app.models.operations import Job, JobStatus
from app.models.organization import Organization
from app.services.task_service import JobNotFoundError, TaskService

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
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Task Test Customer"))
        session.add(
            Job(
                id=job_id, tenant_id=tenant_id, customer_id=customer_id, job_number=f"J-{uuid.uuid4().hex[:6]}",
                title="Task Test Job", status=JobStatus.DRAFT,
            )
        )
        await session.commit()
    return job_id


@requires_real_postgres
async def test_create_and_complete_task_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.task_service as task_service_module

    monkeypatch.setattr(task_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    job_id = await _make_org_and_job(tenant_id)
    service = TaskService(async_session_maker)

    task = await service.create_task(tenant_id, job_id, title="Inspect unit")
    await service.complete_task(tenant_id, task.id, completed_by=None)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_create_task_on_tenant_bs_job() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_org_and_job(tenant_a)
    await _make_org_and_job(tenant_b)
    service = TaskService(async_session_maker)

    with pytest.raises(JobNotFoundError):
        await service.create_task(tenant_b, job_a, title="cross-tenant probe")
