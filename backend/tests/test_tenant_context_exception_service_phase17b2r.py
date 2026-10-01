"""Phase 17B-2R: real-PostgreSQL behavioral proof that ExceptionService's
own, independently-opened sessions (2 sites) now stamp `SET LOCAL
app.tenant_id`. This service is a dependency of many other already-fixed
services this phase (WarrantyService, LicenseService, CampaignService,
RetentionService, ...), so closing its own gap here also completes the
tenant-context chain for all of them.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.organization import Organization
from app.services.exception_service import ExceptionNotFoundError, ExceptionService

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


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


def _service() -> ExceptionService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return ExceptionService(async_session_maker, bus)


@requires_real_postgres
async def test_create_and_resolve_exception_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.exception_service as exception_service_module

    monkeypatch.setattr(exception_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    exc, deduped = await service.create_exception(
        tenant_id, type="INVOICE_OVERDUE", severity="HIGH", entity_type="invoice", entity_id=uuid.uuid4(),
        description="Test exception",
    )
    assert not deduped
    await service.resolve_exception(tenant_id, exc.id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_resolve_tenant_bs_exception() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    exc, _ = await service.create_exception(
        tenant_a, type="INVOICE_OVERDUE", severity="HIGH", entity_type="invoice", entity_id=uuid.uuid4(),
        description="A-only exception",
    )

    with pytest.raises(ExceptionNotFoundError):
        await service.resolve_exception(tenant_b, exc.id)
