import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.services.policy_service import PolicyService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError, ToolBlockedError
from app.tools.policy import ActionPolicy

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_autonomy_stats_reflect_real_outcomes_never_hardcoded(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    # 2 automatic
    await tool_registry.execute("crm.create_customer", {"name": "Auto 1"}, ctx)
    await tool_registry.execute("crm.create_customer", {"name": "Auto 2"}, ctx)

    # 1 approval required
    await policy_service.set_policy(
        tenant_id, "crm.create_lead", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx.actor_id
    )
    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute(
            "crm.create_lead", {"name": "Lead", "source": "REFERRAL", "service_requested": "x"}, ctx
        )

    # 1 blocked
    await policy_service.set_policy(tenant_id, "crm.update_lead", ActionPolicy.BLOCKED, actor_id=ctx.actor_id)
    with pytest.raises(ToolBlockedError):
        await tool_registry.execute("crm.update_lead", {"lead_id": str(uuid.uuid4())}, ctx)

    # 1 failed (real validation failure, not a policy block)
    with pytest.raises(Exception):
        await tool_registry.execute("crm.create_customer", {}, ctx)

    stats = await tool_registry.execute("automation.get_autonomy_stats", {}, ctx)
    assert stats.automatic == 2
    assert stats.approval_required == 1
    assert stats.blocked == 1
    assert stats.failed == 1
    assert stats.total == 5


async def test_autonomy_stats_are_tenant_isolated(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ctx_a, ctx_b = _ctx(tenant_a), _ctx(tenant_b)

    await tool_registry.execute("crm.create_customer", {"name": "A Co"}, ctx_a)

    stats_a = await tool_registry.execute("automation.get_autonomy_stats", {}, ctx_a)
    stats_b = await tool_registry.execute("automation.get_autonomy_stats", {}, ctx_b)
    assert stats_a.automatic >= 1
    assert stats_b.total == 0
