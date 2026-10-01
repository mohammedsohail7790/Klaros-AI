"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 2
independently-opened sessions in app/tools/builtin/organization_tools.py
(GetKillSwitchStatus, SetKillSwitch) now stamp `SET LOCAL
app.tenant_id`."""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.organization import Organization
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


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


@requires_real_postgres
async def test_get_and_set_kill_switch_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.organization_tools as org_tools_module

    monkeypatch.setattr(org_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    ctx = _ctx(tenant_id)

    status = await tool_registry.execute("organization.get_kill_switch_status", {}, ctx)
    assert status.ai_paused is False

    updated = await tool_registry.execute("organization.set_kill_switch", {"active": True}, ctx)
    assert updated.ai_paused is True

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_kill_switch_never_toggles_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)

    await tool_registry.execute("organization.set_kill_switch", {"active": True}, _ctx(tenant_a))

    status_b = await tool_registry.execute("organization.get_kill_switch_status", {}, _ctx(tenant_b))
    assert status_b.ai_paused is False
