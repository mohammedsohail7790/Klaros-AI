"""Phase 17B-2R: real-PostgreSQL behavioral proof that AutomationService's
own, independently-opened sessions (`self._session_factory()`, ~23 sites)
now stamp `SET LOCAL app.tenant_id` before touching tenant-owned rows.

Phase 17B-2 already instrumented the Event Bus / event handlers / MCP /
ToolRegistry / public intake / webhooks / Agent execution / recovery /
reasoning entrypoints, but explicitly scoped OUT the domain services those
entrypoints call into (see PHASE_17B2_TENANT_CONTEXT_PROPAGATION_LOG.md
§12 "known limitations": ~87 files with un-instrumented `session_factory()`
sites). AutomationService is the first service closed by Phase 17B-2R.

Methodology matches `tests/test_tenant_context_propagation_phase17b2.py`
exactly: wrap the real `app.db.session.set_tenant_context` (imported into
`app.services.automation_service` as `set_tenant_context`) with a spy that
calls through to the real implementation and immediately reads back
`current_setting('app.tenant_id', true)` on the SAME session, proving the
`SET LOCAL` genuinely took effect in that transaction — not just that the
function was invoked. Skipped entirely on SQLite.
"""

import uuid

import pytest
from sqlalchemy import text

from app.ai.execution_service import AIExecutionService
from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.automation import TriggerType
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


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


def _service() -> AutomationService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    registry = build_tool_registry(async_session_maker, bus)
    return AutomationService(async_session_maker, AIExecutionService(registry))


@requires_real_postgres
async def test_create_and_publish_automation_sets_tenant_context(monkeypatch, spy) -> None:
    import app.services.automation_service as automation_service_module

    monkeypatch.setattr(automation_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    automation = await service.create_automation(
        tenant_id, name="Welcome Email", description=None, trigger_type=TriggerType.MANUAL,
        trigger_config={}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    await service.get_automation(tenant_id, automation.id)
    await service.list_automations(tenant_id)

    assert len(spy.calls) >= 4
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_automation(spy) -> None:
    """Application-layer isolation proof (RLS itself remains audit-mode —
    §19 of the task spec): tenant B's own service call, scoped by its own
    tenant_id filter (`Automation.tenant_id != tenant_id` -> NotFound), must
    never return tenant A's automation, independent of RLS enforcement."""
    from app.services.automation_service import AutomationNotFoundError

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    automation = await service.create_automation(
        tenant_a, name="A-only automation", description=None, trigger_type=TriggerType.MANUAL,
        trigger_config={}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "x", "body": "y"}}],
        created_by=None,
    )

    with pytest.raises(AutomationNotFoundError):
        await service.get_automation(tenant_b, automation.id)

    tenant_b_list = await service.list_automations(tenant_b)
    assert automation.id not in [a.id for a in tenant_b_list]


@requires_real_postgres
async def test_execution_lifecycle_sets_tenant_context_on_every_session(monkeypatch, spy) -> None:
    """Covers the execution-side session sites: start_execution,
    run_steps/_run_one_step, _complete_execution, get_execution — proving
    the whole synchronous (no-wait) execution path stamps context on each
    of its several independently-opened sessions, not just create/publish."""
    import app.services.automation_service as automation_service_module

    monkeypatch.setattr(automation_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    automation = await service.create_automation(
        tenant_id, name="Lead Note", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": "lead.created"}, condition=None,
        steps=[{"action": "crm.create_note", "params": {"body": "hello"}}],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    spy.calls.clear()  # isolate this test to just the execution path

    execution = await service.start_execution(
        tenant_id, published, version, trigger_type=TriggerType.MANUAL, source_event_id=None,
        entity_type=None, entity_id=None, context={}, triggered_by=None,
    )
    assert execution is not None

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_check_and_dispatch_scheduled_single_tenant_scopes_context(monkeypatch, spy) -> None:
    """`check_and_dispatch_scheduled(tenant_id=<real tenant>)` — the manual
    test-tick API path — is a single-tenant-scoped call despite living in a
    method that ALSO supports a genuine cross-tenant sweep (tenant_id=None,
    the real background worker's own call, deliberately left unscoped here
    per Phase 17B-3, not faked). When a concrete tenant_id is passed, every
    session it opens must set that tenant's context."""
    import app.services.automation_service as automation_service_module

    monkeypatch.setattr(automation_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    service = _service()

    dispatched = await service.check_and_dispatch_scheduled(tenant_id)
    assert dispatched == []  # no SCHEDULE-triggered automations exist yet

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_check_and_dispatch_scheduled_global_sweep_is_not_faked(monkeypatch, spy) -> None:
    """The real background-worker call path — `tenant_id=None` — is a
    genuine cross-tenant system sweep (explicitly classified CROSS_TENANT_
    SYSTEM in PHASE_17B2R_TENANT_SESSION_INVENTORY.md, flagged for Phase
    17B-3's not-yet-built system/global context). This test proves the
    sweep still runs correctly across multiple tenants and that
    `set_tenant_context` is correctly called with `None` (a documented
    no-op, per app/db/session.py's own docstring) rather than a fabricated
    tenant_id."""
    import app.services.automation_service as automation_service_module

    monkeypatch.setattr(automation_service_module, "set_tenant_context", spy)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)
    service = _service()

    # No automations exist for either tenant; the sweep must still run
    # across both without raising and without fabricating a tenant_id.
    dispatched = await service.check_and_dispatch_scheduled(None)
    assert dispatched == []
    assert len(spy.calls) >= 1
    assert spy.calls[0][0] is None
