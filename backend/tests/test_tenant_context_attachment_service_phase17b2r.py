"""Phase 17B-2R: real-PostgreSQL behavioral proof that AttachmentService's
own, independently-opened session (1 site) now stamps `SET LOCAL
app.tenant_id`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Customer
from app.models.operations import AttachmentKind, Job, JobStatus
from app.models.organization import Organization
from app.services.attachment_service import AttachmentService, JobNotFoundError
from app.storage.local_adapter import LocalFilesystemStorageAdapter

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
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Attachment Test Customer"))
        session.add(
            Job(
                id=job_id, tenant_id=tenant_id, customer_id=customer_id, job_number=f"J-{uuid.uuid4().hex[:6]}",
                title="Attachment Test Job", status=JobStatus.DRAFT,
            )
        )
        await session.commit()
    return job_id


@requires_real_postgres
async def test_add_document_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.attachment_service as attachment_service_module

    monkeypatch.setattr(attachment_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    job_id = await _make_org_and_job(tenant_id)
    service = AttachmentService(async_session_maker, LocalFilesystemStorageAdapter("/private/tmp/claude-501/-Users-mohammedsohail-Desktop-Klaros-AI/67bf650e-b0da-4e10-8b67-614af1c48252/scratchpad/attachment_test_storage"))

    await service.add_document(
        tenant_id, job_id, kind=AttachmentKind.PHOTO, filename="before.jpg", content=b"fake-bytes",
        content_type="image/jpeg", uploaded_by=None,
    )

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_add_document_to_tenant_bs_job() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a = await _make_org_and_job(tenant_a)
    await _make_org_and_job(tenant_b)
    service = AttachmentService(async_session_maker, LocalFilesystemStorageAdapter("/private/tmp/claude-501/-Users-mohammedsohail-Desktop-Klaros-AI/67bf650e-b0da-4e10-8b67-614af1c48252/scratchpad/attachment_test_storage"))

    with pytest.raises(JobNotFoundError):
        await service.add_document(
            tenant_b, job_a, kind=AttachmentKind.PHOTO, filename="x.jpg", content=b"x",
            content_type="image/jpeg", uploaded_by=None,
        )
