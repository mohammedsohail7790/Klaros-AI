"""Phase 17B-2R: real-PostgreSQL behavioral proof that NurtureService's
own, independently-opened sessions (5 sites) now stamp `SET LOCAL
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
from app.models.crm import Lead
from app.models.organization import Organization
from app.services.nurture_service import NurtureService, SequenceNotFoundError

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


async def _make_org_and_lead(tenant_id: uuid.UUID) -> uuid.UUID:
    lead_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Lead(id=lead_id, tenant_id=tenant_id, name="Nurture Test Lead", source="web"))
        await session.commit()
    return lead_id


def _service() -> NurtureService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return NurtureService(async_session_maker, bus)


@requires_real_postgres
async def test_create_sequence_and_enroll_lead_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.nurture_service as nurture_service_module

    monkeypatch.setattr(nurture_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    lead_id = await _make_org_and_lead(tenant_id)
    service = _service()

    sequence = await service.create_sequence(tenant_id, name="Re-engage", trigger_type="STALE_LEAD")
    await service.enroll_lead(tenant_id, sequence.id, lead_id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_enroll_lead_in_tenant_bs_sequence() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_a = await _make_org_and_lead(tenant_a)
    await _make_org_and_lead(tenant_b)
    service = _service()

    sequence = await service.create_sequence(tenant_a, name="A-only Sequence", trigger_type="STALE_LEAD")

    with pytest.raises(SequenceNotFoundError):
        await service.enroll_lead(tenant_b, sequence.id, lead_a)
