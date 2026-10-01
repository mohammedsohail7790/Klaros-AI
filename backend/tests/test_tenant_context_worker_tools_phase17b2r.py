"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 3
independently-opened sessions in app/tools/builtin/worker_tools.py
(CreateWorker, ListWorkers, UpdateWorkerStatus) now stamp `SET LOCAL
app.tenant_id`."""

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
async def test_create_list_update_worker_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.worker_tools as worker_tools_module

    monkeypatch.setattr(worker_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    created = await tool_registry.execute("operations.create_worker", {"name": "Test Worker"}, ctx)
    worker_id = created.worker["id"]

    listed = await tool_registry.execute("operations.list_workers", {}, ctx)
    assert any(w["id"] == worker_id for w in listed.workers)

    updated = await tool_registry.execute(
        "operations.update_worker_status", {"worker_id": worker_id, "status": "OFFLINE"}, ctx
    )
    assert updated.worker["status"] == "OFFLINE"

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_worker_never_visible_to_tenant_b(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    created = await tool_registry.execute("operations.create_worker", {"name": "A's Worker"}, _ctx(tenant_a))

    listed_b = await tool_registry.execute("operations.list_workers", {}, _ctx(tenant_b))
    assert listed_b.workers == []

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "operations.update_worker_status",
            {"worker_id": created.worker["id"], "status": "OFFLINE"},
            _ctx(tenant_b),
        )
