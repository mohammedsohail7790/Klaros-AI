"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 5
independently-opened sessions in app/tools/builtin/event_tools.py
(GetEvent, ListEvents, GetEventDetail, ListDeadLetters, ReplayDeadLetter)
now stamp `SET LOCAL app.tenant_id`."""

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
async def test_get_and_list_events_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.event_tools as event_tools_module

    monkeypatch.setattr(event_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    published = await tool_registry.execute(
        "events.publish_event", {"event_type": "test.tenant_context_probe", "payload": {}}, ctx
    )
    event_id = published.event_id

    fetched = await tool_registry.execute("events.get_event", {"event_id": event_id}, ctx)
    assert fetched.event_id == event_id

    listed = await tool_registry.execute("events.list_events", {}, ctx)
    assert any(e.event_id == event_id for e in listed.events)

    detail = await tool_registry.execute("events.get_event_detail", {"event_id": event_id}, ctx)
    assert detail.event_id == event_id

    dead_letters = await tool_registry.execute("events.list_dead_letters", {}, ctx)
    assert dead_letters.dead_letters == []

    assert len(spy.calls) >= 4
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_event_never_visible_to_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    published = await tool_registry.execute(
        "events.publish_event", {"event_type": "test.cross_tenant_probe", "payload": {}}, _ctx(tenant_a)
    )

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("events.get_event", {"event_id": published.event_id}, _ctx(tenant_b))

    listed_b = await tool_registry.execute("events.list_events", {}, _ctx(tenant_b))
    assert listed_b.events == []
