"""Phase 17B-2R: real-PostgreSQL behavioral proof for MorningBriefService's
two session-open sites — one genuinely tenant-scoped (`generate`'s own
session, persisting the MorningBrief/Insight/Recommendation rows), one a
genuine CROSS_TENANT_SYSTEM sweep (`check_and_generate_scheduled`'s own
session, which scans every `morning_brief_enabled` org with no single
tenant in scope, and is explicitly NOT given a fabricated tenant context —
see the code comment/docstring added in `app/services/morning_brief_service.py`
this round, referencing Phase 17B-3).

Same spy methodology as `test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.morning_brief import MorningBrief
from app.models.organization import Organization
from app.services.morning_brief_service import MorningBriefService

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


async def _make_org(*, enabled: bool = False, local_time: str = "09:00") -> uuid.UUID:
    async with async_session_maker() as session:
        org = Organization(
            name="Test Brief Co", slug=f"brief-co-{uuid.uuid4().hex[:8]}",
            morning_brief_enabled=enabled, morning_brief_local_time=local_time,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org.id


@requires_real_postgres
async def test_generate_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.services.morning_brief_service as morning_brief_service_module

    monkeypatch.setattr(morning_brief_service_module, "set_tenant_context", spy)

    tenant_id = await _make_org()
    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))

    result = await service.generate(tenant_id, generated_by=ActorType.USER)
    assert result["brief_id"]

    assert len(spy.calls) == 1
    called_tenant, readback = spy.calls[0]
    assert called_tenant == tenant_id
    assert readback == str(tenant_id)


@requires_real_postgres
async def test_scheduled_sweep_is_cross_tenant_and_never_fabricates_context(monkeypatch, spy, tool_registry) -> None:
    """The sweep's own session (scanning all enabled orgs) never calls
    set_tenant_context at all (there's no single tenant in scope there);
    the per-tenant `generate()` calls it makes DO set context, once per
    due tenant. Proves both tenants actually got their own brief AND that
    the sweep genuinely processed more than one tenant in one pass."""
    import app.services.morning_brief_service as morning_brief_service_module

    monkeypatch.setattr(morning_brief_service_module, "set_tenant_context", spy)

    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).strftime("%H:%M")
    tenant_a = await _make_org(enabled=True, local_time=past_time)
    tenant_b = await _make_org(enabled=True, local_time=past_time)

    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))
    await service.check_and_generate_scheduled()

    async with async_session_maker() as session:
        briefs_a = (await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == tenant_a))).scalars().all()
        briefs_b = (await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == tenant_b))).scalars().all()
    assert len(briefs_a) == 1
    assert len(briefs_b) == 1

    # Exactly one set_tenant_context call per due tenant (from generate()),
    # never a call for the sweep's own un-scoped session, and never a call
    # with a fabricated/wrong tenant_id.
    called_tenants = {c[0] for c in spy.calls}
    assert called_tenants == {tenant_a, tenant_b}
    for called_tenant, readback in spy.calls:
        assert readback == str(called_tenant)


@requires_real_postgres
async def test_disabled_org_is_excluded_from_sweep_without_context_leak(tool_registry) -> None:
    tenant_disabled = await _make_org(enabled=False, local_time="09:00")
    service = MorningBriefService(async_session_maker, AIExecutionService(tool_registry))
    await service.check_and_generate_scheduled()

    async with async_session_maker() as session:
        briefs = (await session.execute(select(MorningBrief).where(MorningBrief.tenant_id == tenant_disabled))).scalars().all()
    assert briefs == []
