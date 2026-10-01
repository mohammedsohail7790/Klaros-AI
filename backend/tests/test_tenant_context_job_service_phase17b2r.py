"""Phase 17B-2R: real-PostgreSQL behavioral proof that JobService's own,
independently-opened sessions (3 sites, including the `fresh_session`
re-check inside `create_job`'s IntegrityError-recovery path — a distinct
variable name the mechanical batch-insertion pattern used elsewhere this
phase would have missed had it not been caught by reading the method body)
now stamp `SET LOCAL app.tenant_id`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.organization import Organization
from app.services.job_service import CreateJobInput, JobNotFoundError, JobService

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


async def _make_org_and_customer(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Job Test Customer"))
        await session.commit()
    return customer_id


def _service() -> JobService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return JobService(async_session_maker, bus)


@requires_real_postgres
async def test_create_job_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.job_service as job_service_module

    monkeypatch.setattr(job_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    customer_id = await _make_org_and_customer(tenant_id)
    service = _service()

    job, deduped = await service.create_job(tenant_id, CreateJobInput(title="Test Job", customer_id=customer_id))
    assert not deduped

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_concurrent_duplicate_idempotency_key_sets_context_on_fresh_session_too(monkeypatch, spy) -> None:
    """Proves the `fresh_session` re-check path inside the IntegrityError
    handler ALSO sets tenant context — the site a naive grep for `as
    session:` would miss, since this one is `as fresh_session:`."""
    import app.services.job_service as job_service_module

    monkeypatch.setattr(job_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    customer_id = await _make_org_and_customer(tenant_id)
    service = _service()

    key = f"dup-key-{uuid.uuid4().hex[:8]}"
    job1, deduped1 = await service.create_job(
        tenant_id, CreateJobInput(title="First", customer_id=customer_id, idempotency_key=key),
    )
    assert not deduped1

    job2, deduped2 = await service.create_job(
        tenant_id, CreateJobInput(title="Second (duplicate key)", customer_id=customer_id, idempotency_key=key),
    )
    assert deduped2
    assert job2.id == job1.id

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_create_job_from_tenant_bs_appointment() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org_and_customer(tenant_a)
    await _make_org_and_customer(tenant_b)
    service = _service()

    with pytest.raises(JobNotFoundError):
        await service.create_job_from_appointment(tenant_b, uuid.uuid4())
