"""Phase 10: AI recommendations must obey PolicyService — the AI never
decides whether an action is safe, it only ever calls the same
ToolRegistry.execute() every human call goes through, which resolves the
tenant's real, current policy. These tests prove a tenant's policy choice
actually changes what happens when a recommendation is executed — not the
AI's own judgment."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.services.policy_service import PolicyService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolBlockedError
from app.tools.policy import ActionPolicy

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _seed_reward_recommendation(tool_registry, tenant_id, ctx):
    program = await tool_registry.execute(
        "retention.create_referral_program",
        {"name": "Refer a Friend", "reward_type": "credit", "reward_amount": "25.00"},
        ctx,
    )
    referrer = await tool_registry.execute("crm.create_customer", {"name": "Referrer"}, ctx)
    code = await tool_registry.execute(
        "retention.get_or_create_referral_code",
        {"program_id": program.program_id, "customer_id": referrer.customer["id"]},
        ctx,
    )
    referral = await tool_registry.execute("retention.create_referral", {"referral_code_id": code.code_id}, ctx)
    await tool_registry.execute(
        "retention.convert_referral_to_lead", {"referral_id": referral.referral_id, "name": "Referred"}, ctx
    )
    await tool_registry.execute(
        "retention.request_referral_reward", {"referral_id": referral.referral_id, "amount": "25.00"}, ctx
    )
    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)
    return next(r for r in latest.recommendations if r.executable and "reward" in r.what.lower())


async def test_auto_policy_recommendation_executes_without_approval(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    rec = await _seed_reward_recommendation(tool_registry, tenant_id, ctx)

    result = await tool_registry.execute(
        "insights.execute_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
    )
    assert result.status == "EXECUTED"


async def test_approval_required_policy_recommendation_creates_approval_not_execution(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await policy_service.set_policy(
        tenant_id, "retention.approve_referral_reward", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx.actor_id
    )
    rec = await _seed_reward_recommendation(tool_registry, tenant_id, ctx)

    result = await tool_registry.execute(
        "insights.execute_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
    )
    assert result.status == "APPROVAL_REQUESTED"
    assert result.approval_request_id


async def test_blocked_policy_recommendation_cannot_execute(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await policy_service.set_policy(
        tenant_id, "retention.approve_referral_reward", ActionPolicy.BLOCKED, actor_id=ctx.actor_id
    )
    rec = await _seed_reward_recommendation(tool_registry, tenant_id, ctx)

    with pytest.raises(ToolBlockedError):
        await tool_registry.execute(
            "insights.execute_recommendation", {"recommendation_id": rec.recommendation_id}, ctx
        )


async def test_policy_change_between_tenants_is_respected_independently(tool_registry) -> None:
    """The same recommendation type, same tool, different tenants, opposite
    policies — proving the AI has no say in it, only PolicyService per
    tenant does."""
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_auto, tenant_blocked = uuid.uuid4(), uuid.uuid4()
    ctx_auto, ctx_blocked = _ctx(tenant_auto), _ctx(tenant_blocked)

    await policy_service.set_policy(
        tenant_blocked, "retention.approve_referral_reward", ActionPolicy.BLOCKED, actor_id=ctx_blocked.actor_id
    )

    rec_auto = await _seed_reward_recommendation(tool_registry, tenant_auto, ctx_auto)
    rec_blocked = await _seed_reward_recommendation(tool_registry, tenant_blocked, ctx_blocked)

    result_auto = await tool_registry.execute(
        "insights.execute_recommendation", {"recommendation_id": rec_auto.recommendation_id}, ctx_auto
    )
    assert result_auto.status == "EXECUTED"

    with pytest.raises(ToolBlockedError):
        await tool_registry.execute(
            "insights.execute_recommendation", {"recommendation_id": rec_blocked.recommendation_id}, ctx_blocked
        )


async def test_ai_actor_cannot_call_automation_set_policy(tool_registry) -> None:
    """Role.MANAGER (the role AIExecutionService always assigns an AI
    caller, see app/ai/execution_service.py) legitimately holds
    MANAGE_AUTOMATION_POLICIES for its human members — so permission alone
    would let an AI-actor call through. The AI must never decide what it's
    allowed to do automatically; SetPolicy/ResetPolicy each carry an
    explicit actor_type guard on top of the permission check, the same
    pattern Phase 9 used for approvals.approve/reject."""
    tenant_id = uuid.uuid4()
    ai_ctx = ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None, role=Role.MANAGER)

    with pytest.raises(ValueError, match="AI cannot change automation policy"):
        await tool_registry.execute(
            "automation.set_policy", {"tool_name": "crm.create_customer", "policy": "BLOCKED"}, ai_ctx
        )

    with pytest.raises(ValueError, match="AI cannot change automation policy"):
        await tool_registry.execute(
            "automation.reset_policy", {"tool_name": "crm.create_customer"}, ai_ctx
        )

    # Policy is genuinely untouched — the AI's attempt had zero effect.
    policy_service = PolicyService(tool_registry._session_factory)
    assert await policy_service.resolve(tenant_id, "crm.create_customer") == ActionPolicy.AUTO
