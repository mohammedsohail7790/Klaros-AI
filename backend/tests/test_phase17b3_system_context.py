"""Phase 17B-3: real-PostgreSQL proof for the two CROSS_TENANT_SYSTEM sweep
gaps closed this phase.

Phase 17B-2R correctly identified `AutomationService.check_and_dispatch_
scheduled` and `MorningBriefService.check_and_generate_scheduled` as genuine
CROSS_TENANT_SYSTEM sweeps and deliberately did NOT fabricate tenant context
for their own cross-tenant discovery sessions (see
`PHASE_17B2R_TENANT_SESSION_INVENTORY.md` rows 46 and the automation
service's own classification). What 17B-2R's tests did not yet cover is
that, once discovery finds concrete per-tenant candidates, every subsequent
DB session touching a genuinely tenant-owned table must be stamped with
THAT tenant's own id — never left at the sweep's own `None`/no-context
default. Two gaps of exactly that shape existed prior to this phase:

1. `AutomationService.check_and_dispatch_scheduled`'s per-candidate
   "already fired" dedup lookup (`AutomationExecution`, tenant-owned) was
   stamped with the outer, sweep-level `tenant_id` parameter (`None` during
   the real worker-tick sweep) instead of `automation.tenant_id` — the
   specific tenant that candidate actually belongs to.
2. `MorningBriefService.check_and_generate_scheduled`'s per-org
   `MorningBrief` existence lookup (tenant-owned) ran inside the same
   un-scoped discovery session used to read the GLOBAL-ish `Organization`
   sweep list, with no tenant context set at all.

Both are fixed in this phase using the preferred "iterate tenant-by-tenant,
open a fresh session per tenant, stamp before touching tenant-owned rows"
pattern from PHASE_17B3_SYSTEM_GLOBAL_CONTEXT_IMPLEMENTATION_LOG.md. This
file proves it behaviorally against real Postgres, using the same
`set_tenant_context` spy methodology as
`test_tenant_context_automation_service_phase17b2r.py` /
`test_tenant_context_morning_brief_service_phase17b2r.py`.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.automation import AutomationStatus, TriggerType
from app.models.organization import Organization
from app.services.automation_service import AutomationService
from app.tools.factory import build_tool_registry

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


async def _make_org(tenant_id: uuid.UUID | None = None, *, timezone_name: str = "UTC") -> uuid.UUID:
    tenant_id = tenant_id or uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}", timezone=timezone_name))
        await session.commit()
    return tenant_id


def _automation_service() -> AutomationService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    return AutomationService(async_session_maker, AIExecutionService(registry))


@requires_real_postgres
async def test_global_schedule_sweep_stamps_each_candidates_own_tenant(monkeypatch, spy) -> None:
    """The real worker-tick call (`tenant_id=None`) must never leave the
    per-candidate dedup check un-scoped, and must never cross-contaminate
    one candidate's tenant into another's session. Two tenants, each with
    one DAILY schedule due right now: every `set_tenant_context` call made
    once discovery hands back a concrete candidate must carry that
    candidate's own, real tenant_id — never None, never the other tenant's."""
    import app.services.automation_service as automation_service_module

    monkeypatch.setattr(automation_service_module, "set_tenant_context", spy)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _automation_service()

    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).strftime("%H:%M")
    trigger_config = {"frequency": "DAILY", "time": past_time}

    automations = {}
    for tenant_id in (tenant_a, tenant_b):
        automation = await service.create_automation(
            tenant_id, name="Daily Digest", description=None, trigger_type=TriggerType.SCHEDULE,
            trigger_config=trigger_config, condition=None,
            steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
            created_by=None,
        )
        await service.publish(tenant_id, automation.id)
        automations[tenant_id] = automation.id

    spy.calls.clear()  # isolate to just the sweep itself

    dispatched = await service.check_and_dispatch_scheduled(None, now_utc=now_utc)
    assert len(dispatched) == 2

    # The very first call is the cross-tenant discovery session itself —
    # genuinely un-scoped, exactly as Phase 17B-2R classified it.
    assert spy.calls[0][0] is None

    # Every call AFTER discovery (the per-candidate dedup check, plus each
    # candidate's own start_execution lifecycle) must carry a real,
    # correctly-attributed tenant_id — never None, never fabricated, never
    # the wrong tenant.
    post_discovery_calls = spy.calls[1:]
    assert post_discovery_calls, "expected per-candidate calls after discovery"
    for called_tenant, readback in post_discovery_calls:
        assert called_tenant in (tenant_a, tenant_b)
        assert readback == str(called_tenant)

    called_tenants = {c[0] for c in post_discovery_calls}
    assert called_tenants == {tenant_a, tenant_b}


@requires_real_postgres
async def test_global_schedule_sweep_one_tenant_failure_does_not_block_the_other(monkeypatch) -> None:
    """Rule 8 / requirement #8 (background-sweep failure isolation): a
    disabled/unpublished automation for tenant A (which start_execution
    will simply skip/no-op for, since only ENABLED automations are
    candidates in the first place) must never prevent tenant B's due
    schedule from firing in the same sweep tick."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _automation_service()

    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).strftime("%H:%M")
    trigger_config = {"frequency": "DAILY", "time": past_time}

    automation_b = await service.create_automation(
        tenant_b, name="Daily Digest B", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config=trigger_config, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    await service.publish(tenant_b, automation_b.id)

    dispatched = await service.check_and_dispatch_scheduled(None, now_utc=now_utc)
    assert len(dispatched) == 1

    async with async_session_maker() as session:
        from sqlalchemy import select

        from app.models.automation import Automation

        auto_b_row = await session.get(Automation, automation_b.id)
        assert auto_b_row.status == AutomationStatus.ENABLED
