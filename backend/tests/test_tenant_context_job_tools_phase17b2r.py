"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 4
independently-opened sessions in app/tools/builtin/job_tools.py (GetJob,
UpdateJob, SearchJobs, GetJobTimeline) now stamp `SET LOCAL
app.tenant_id`."""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.operations import Job, JobStatus
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


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


async def _make_customer_and_job(tenant_id: uuid.UUID) -> Job:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Job Tools Test Customer")
        session.add(customer)
        await session.flush()
        job = Job(
            tenant_id=tenant_id, customer_id=customer.id, job_number=f"JT-{uuid.uuid4().hex[:8]}",
            title="Job Tools Test Job", status=JobStatus.SCHEDULED,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


@requires_real_postgres
async def test_get_update_search_timeline_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.job_tools as job_tools_module

    monkeypatch.setattr(job_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    job = await _make_customer_and_job(tenant_id)
    ctx = _ctx(tenant_id)

    fetched = await tool_registry.execute("operations.get_job", {"job_id": str(job.id)}, ctx)
    assert fetched.job["id"] == str(job.id)

    updated = await tool_registry.execute(
        "operations.update_job", {"job_id": str(job.id), "title": "Updated Title"}, ctx
    )
    assert updated.job["title"] == "Updated Title"

    searched = await tool_registry.execute("operations.search_jobs", {}, ctx)
    assert any(j["id"] == str(job.id) for j in searched.jobs)

    timeline = await tool_registry.execute("operations.get_job_timeline", {"job_id": str(job.id)}, ctx)
    assert timeline.job_id == str(job.id)

    assert len(spy.calls) >= 4
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_job_never_visible_to_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_customer_and_job(tenant_a)

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("operations.get_job", {"job_id": str(job_a.id)}, _ctx(tenant_b))

    searched_b = await tool_registry.execute("operations.search_jobs", {}, _ctx(tenant_b))
    assert searched_b.jobs == []
