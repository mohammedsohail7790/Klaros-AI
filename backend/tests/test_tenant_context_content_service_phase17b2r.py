"""Phase 17B-2R: real-PostgreSQL behavioral proof that ContentService's own,
independently-opened sessions (8 sites) now stamp `SET LOCAL
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
from app.services.ai_provider import get_ai_provider
from app.services.content_service import ContentService, JobNotFoundError

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


async def _make_org_customer_job(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Content Test Customer"))
        session.add(
            Job(
                id=job_id, tenant_id=tenant_id, customer_id=customer_id, job_number=f"J-{uuid.uuid4().hex[:6]}",
                title="Test Job", service_type="HVAC", status=JobStatus.DRAFT,
            )
        )
        await session.commit()
    return job_id


def _service() -> ContentService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return ContentService(async_session_maker, bus, get_ai_provider())


@requires_real_postgres
async def test_create_idea_and_generate_draft_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.content_service as content_service_module

    monkeypatch.setattr(content_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    job_id = await _make_org_customer_job(tenant_id)
    service = _service()

    await service.create_idea(tenant_id, title="Idea", summary=None, created_by=None)
    await service.generate_draft_from_job(tenant_id, job_id, None)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_generate_draft_from_tenant_bs_job() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_org_customer_job(tenant_a)
    await _make_org_customer_job(tenant_b)
    service = _service()

    with pytest.raises(JobNotFoundError):
        await service.generate_draft_from_job(tenant_b, job_a, None)
