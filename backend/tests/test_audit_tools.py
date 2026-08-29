import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


async def test_record_action_writes_a_manual_audit_row(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    result = await tool_registry.execute(
        "audit.record_action", {"action": "manual.review", "note": "reviewed offline"}, ctx
    )
    assert result.audit_log_id


async def test_list_ai_activity_includes_ai_calls_and_approval_lifecycle(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    activity = await tool_registry.execute("audit.list_ai_activity", {}, ctx)

    assert any(r.tool == "insights.get_finance_snapshot" and r.actor_type == "AI" for r in activity.rows)


async def test_list_ai_activity_is_tenant_isolated(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await tool_registry.execute("insights.generate_morning_brief", {}, _ctx(tenant_a))

    activity_b = await tool_registry.execute("audit.list_ai_activity", {}, _ctx(tenant_b))
    assert activity_b.rows == []
