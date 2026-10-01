"""Phase 17B-2R: real-PostgreSQL behavioral proof that the 2
independently-opened sessions in app/tools/builtin/audit_tools.py
(RecordAction, ListAIActivity) now stamp `SET LOCAL app.tenant_id`.
Tenant isolation itself is already covered by the pre-existing
tests/test_audit_tools.py::test_list_ai_activity_is_tenant_isolated —
this file adds only the GUC-propagation proof that phase requires."""

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
async def test_record_action_and_list_ai_activity_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.audit_tools as audit_tools_module

    monkeypatch.setattr(audit_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    result = await tool_registry.execute("audit.record_action", {"action": "manual.review", "note": "n/a"}, ctx)
    assert result.audit_log_id

    activity = await tool_registry.execute("audit.list_ai_activity", {}, ctx)
    assert activity.rows == []  # no AI-actor rows yet, but the query itself must have run tenant-scoped

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
