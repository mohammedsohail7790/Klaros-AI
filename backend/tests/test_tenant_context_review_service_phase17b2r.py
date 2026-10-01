"""Phase 17B-2R: real-PostgreSQL behavioral proof that ReviewService's own,
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
from app.models.organization import Organization
from app.models.retention import ReviewRequest, ReviewStatus
from app.services.exception_service import ExceptionService
from app.services.retention_service import RetentionService
from app.services.review_service import FeedbackNotFoundError, ReviewService

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


async def _make_org_customer_and_review(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    review_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Review Test Customer"))
        session.add(
            ReviewRequest(
                id=review_id, tenant_id=tenant_id, customer_id=customer_id, status=ReviewStatus.ELIGIBLE,
            )
        )
        await session.commit()
    return review_id


def _service() -> ReviewService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    exceptions = ExceptionService(async_session_maker, bus)
    retention = RetentionService(async_session_maker, bus, exceptions)
    return ReviewService(async_session_maker, bus, exceptions, retention)


@requires_real_postgres
async def test_send_review_request_and_record_feedback_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.review_service as review_service_module

    monkeypatch.setattr(review_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    review_id = await _make_org_customer_and_review(tenant_id)
    service = _service()

    await service.send_review_request(tenant_id, review_id)
    feedback = await service.record_feedback(
        tenant_id, customer_id=uuid.uuid4(), job_id=None, rating=5, comment="Great!",
    )
    await service.record_consent(tenant_id, feedback.id, consent=True)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_record_consent_on_tenant_bs_feedback() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org_customer_and_review(tenant_a)
    await _make_org_customer_and_review(tenant_b)
    service = _service()

    feedback_a = await service.record_feedback(
        tenant_a, customer_id=uuid.uuid4(), job_id=None, rating=4, comment=None,
    )

    with pytest.raises(FeedbackNotFoundError):
        await service.record_consent(tenant_b, feedback_a.id, consent=True)
