"""Phase 17B-2R: real-PostgreSQL behavioral proof that VoiceCallService's
own, independently-opened sessions (7 sites) now stamp `SET LOCAL
app.tenant_id`. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.services.voice_call_service import CallSessionNotFoundError, VoiceCallService

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


@requires_real_postgres
async def test_get_or_create_call_and_end_call_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.voice_call_service as vcs_module

    monkeypatch.setattr(vcs_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = VoiceCallService(async_session_maker)

    call, created = await service.get_or_create_call(
        tenant_id, provider="twilio", external_call_id=f"CA{uuid.uuid4().hex}", caller_number="+15551234567",
    )
    assert created
    await service.end_call(tenant_id, call.id, outcome="completed")
    await service.get_call(tenant_id, call.id)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_call() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = VoiceCallService(async_session_maker)

    call, _ = await service.get_or_create_call(
        tenant_a, provider="twilio", external_call_id=f"CA{uuid.uuid4().hex}", caller_number=None,
    )

    with pytest.raises(CallSessionNotFoundError):
        await service.get_call(tenant_b, call.id)
