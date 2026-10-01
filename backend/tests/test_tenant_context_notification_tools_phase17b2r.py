"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 1
independently-opened session in
app/tools/builtin/notification_tools.py (CreateNotification) now stamps
`SET LOCAL app.tenant_id`."""

import uuid

import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.notification import Notification
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
async def test_create_notification_sets_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.notification_tools as notification_tools_module

    monkeypatch.setattr(notification_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    result = await tool_registry.execute(
        "notifications.create_notification", {"title": "Test", "body": "Test body"}, ctx
    )
    assert result.notification_id

    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_notification_is_stamped_with_the_correct_tenant(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    result = await tool_registry.execute(
        "notifications.create_notification", {"title": "A's note", "body": "..."}, _ctx(tenant_a)
    )

    async with async_session_maker() as session:
        rows_b = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_b))
        ).scalars().all()
        assert rows_b == []

        row = await session.get(Notification, uuid.UUID(result.notification_id))
        assert row.tenant_id == tenant_a
