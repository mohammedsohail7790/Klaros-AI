"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in
app/tools/builtin/automation_policy_tools.py (GetAutonomyStats) now
stamps `SET LOCAL app.tenant_id`."""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


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


@requires_real_postgres
async def test_get_autonomy_stats_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.automation_policy_tools as apt_module

    monkeypatch.setattr(apt_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    # Generate at least one real tool.execute:* audit row for this tenant.
    await tool_registry.execute("audit.record_action", {"action": "manual.review", "note": "n/a"}, ctx)

    result = await tool_registry.execute("automation.get_autonomy_stats", {}, ctx)
    assert result is not None

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_autonomy_stats_isolated_per_tenant(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await tool_registry.execute("crm.create_lead", {"name": "A Lead", "source": "web"}, _ctx(tenant_a))

    stats_b = await tool_registry.execute("automation.get_autonomy_stats", {}, _ctx(tenant_b))
    assert stats_b.automatic == 0
