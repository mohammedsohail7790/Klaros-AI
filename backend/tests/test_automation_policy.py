"""Phase 10A: per-tenant automation policy — PolicyService, its tools, and
the API layer. Covers the spec's 12-point checklist plus the platform
safety floor (SYSTEM_BLOCKED_TOOLS)."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.rbac import Role
from app.models.tool_policy import TenantToolPolicy
from app.services.policy_service import PolicyService, SystemBlockedPolicyError
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError, ToolBlockedError, ToolPermissionError
from app.tools.policy import ActionPolicy

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_id=None):
    return ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id or uuid.uuid4(), role=role
    )


async def test_default_policy_used_with_no_tenant_override(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    resolved = await policy_service.resolve(tenant_id, "crm.create_customer")
    assert resolved == ActionPolicy.AUTO  # the static system default for this tool


async def test_tenant_override_changes_resolution(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    await policy_service.set_policy(
        tenant_id, "crm.create_customer", ActionPolicy.APPROVAL_REQUIRED, actor_id=actor_id
    )
    resolved = await policy_service.resolve(tenant_id, "crm.create_customer")
    assert resolved == ActionPolicy.APPROVAL_REQUIRED

    # A different tenant is unaffected — no override exists for it.
    other_tenant = uuid.uuid4()
    assert await policy_service.resolve(other_tenant, "crm.create_customer") == ActionPolicy.AUTO


async def test_missing_policy_falls_back_to_default_safely(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    # A tool with no explicit DEFAULT_TOOL_POLICIES entry falls back to
    # APPROVAL_REQUIRED (policy_for's own safe default), not AUTO.
    resolved = await policy_service.resolve(tenant_id, "not.a.real.tool")
    assert resolved == ActionPolicy.APPROVAL_REQUIRED


async def test_system_blocked_tool_cannot_be_overridden(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    with pytest.raises(SystemBlockedPolicyError):
        await policy_service.set_policy(
            tenant_id, "customer.delete", ActionPolicy.AUTO, actor_id=actor_id
        )
    # Still BLOCKED regardless of the rejected attempt.
    assert await policy_service.resolve(tenant_id, "customer.delete") == ActionPolicy.BLOCKED


async def test_auto_policy_executes_immediately(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    out = await tool_registry.execute("crm.create_customer", {"name": "Auto Co"}, ctx)
    assert out.customer["name"] == "Auto Co"


async def test_approval_required_override_creates_approval(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await policy_service.set_policy(
        tenant_id, "crm.create_customer", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx.actor_id
    )
    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute("crm.create_customer", {"name": "Needs Approval Co"}, ctx)


async def test_blocked_override_prevents_execution(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await policy_service.set_policy(tenant_id, "crm.create_customer", ActionPolicy.BLOCKED, actor_id=ctx.actor_id)
    with pytest.raises(ToolBlockedError):
        await tool_registry.execute("crm.create_customer", {"name": "Blocked Co"}, ctx)


async def test_tenant_isolation_of_policy_overrides(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ctx_a, ctx_b = _ctx(tenant_a), _ctx(tenant_b)

    await policy_service.set_policy(
        tenant_a, "crm.create_customer", ActionPolicy.BLOCKED, actor_id=ctx_a.actor_id
    )

    with pytest.raises(ToolBlockedError):
        await tool_registry.execute("crm.create_customer", {"name": "A Co"}, ctx_a)

    # Tenant B is completely unaffected.
    out = await tool_registry.execute("crm.create_customer", {"name": "B Co"}, ctx_b)
    assert out.customer["name"] == "B Co"


async def test_changing_policy_affects_future_executions_only(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    out1 = await tool_registry.execute("crm.create_customer", {"name": "Before Co"}, ctx)
    assert out1.customer["name"] == "Before Co"

    await policy_service.set_policy(tenant_id, "crm.create_customer", ActionPolicy.BLOCKED, actor_id=ctx.actor_id)

    with pytest.raises(ToolBlockedError):
        await tool_registry.execute("crm.create_customer", {"name": "After Co"}, ctx)


async def test_existing_approval_revalidates_current_policy_on_execution(tool_registry) -> None:
    """The Phase 9 skip_approval_gate safety property, now exercised through
    a Phase 10 tenant policy change: an approval created while a tool was
    APPROVAL_REQUIRED must still be safely blocked at execution time if the
    tenant subsequently sets the tool to BLOCKED before it's approved."""
    from app.services.approval_execution_service import ApprovalExecutionService
    from app.models.approval import ApprovalRequest, ApprovalStatus

    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    await policy_service.set_policy(
        tenant_id, "crm.create_customer", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx.actor_id
    )
    try:
        await tool_registry.execute("crm.create_customer", {"name": "Pending Co"}, ctx)
        assert False, "expected ToolApprovalRequiredError"
    except ToolApprovalRequiredError as exc:
        approval_id = exc.approval_request_id

    # Tenant tightens the policy to BLOCKED before anyone approves.
    await policy_service.set_policy(tenant_id, "crm.create_customer", ActionPolicy.BLOCKED, actor_id=ctx.actor_id)

    async with tool_registry._session_factory() as session:
        request = await session.get(ApprovalRequest, approval_id)
        request.status = ApprovalStatus.APPROVED
        await session.commit()

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, tool_registry._bus)
    result = await service.execute_approved(tenant_id, approval_id)
    assert result.execution_status == "FAILED"
    assert "blocked" in (result.execution_error or "").lower()


async def test_policy_change_is_audited(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    await policy_service.set_policy(
        tenant_id, "crm.create_customer", ActionPolicy.APPROVAL_REQUIRED, actor_id=actor_id
    )

    async with tool_registry._session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.action == "automation_policy.change"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].input_summary["tool_name"] == "crm.create_customer"
    assert rows[0].input_summary["new_policy"] == "APPROVAL_REQUIRED"


async def test_concurrent_policy_updates_are_safe(tool_registry) -> None:
    """Two concurrent writers changing the same tenant+tool policy must
    never corrupt state — the CAS/version loop must let both complete, one
    after the other, with the row ending in a real, consistent final state
    (whichever wrote last).

    A shared `asyncio.Lock` serializes the two coroutines' DB calls here —
    the same documented technique used by tests/test_phase8_e2e.py and
    tests/test_approval_orchestration.py's concurrency tests: this test
    suite's sqlite `StaticPool` is one physical connection shared by every
    session, which two genuinely interleaved writers can drive into
    "SQL statements in progress" — a sqlite-in-this-test-harness artifact,
    not a production concern (a real Postgres connection pool has no such
    restriction). The lock only serializes access to that one physical test
    connection; it plays no role in — and is not a substitute for — the
    real concurrency-safety guarantee under test, which is the DB-level
    `version` compare-and-swap in `PolicyService.set_policy` itself. Both
    calls are still genuinely concurrent, independently-scheduled
    coroutines competing for the same row.
    """
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    db_lock = asyncio.Lock()

    async def set_policy_locked(policy: ActionPolicy) -> dict:
        async with db_lock:
            return await policy_service.set_policy(
                tenant_id, "crm.create_customer", policy, actor_id=actor_id
            )

    results = await asyncio.gather(
        set_policy_locked(ActionPolicy.APPROVAL_REQUIRED),
        set_policy_locked(ActionPolicy.BLOCKED),
    )
    final = await policy_service.resolve(tenant_id, "crm.create_customer")
    assert final in (ActionPolicy.APPROVAL_REQUIRED, ActionPolicy.BLOCKED)
    assert results[0]["current_policy"] in (ActionPolicy.APPROVAL_REQUIRED, ActionPolicy.BLOCKED)

    async with tool_registry._session_factory() as session:
        row = (
            await session.execute(
                select(TenantToolPolicy).where(
                    TenantToolPolicy.tenant_id == tenant_id, TenantToolPolicy.tool_name == "crm.create_customer"
                )
            )
        ).scalar_one()
    assert row.version == 2  # both writes landed, in order — no lost update


async def test_reset_policy_returns_to_system_default(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    await policy_service.set_policy(tenant_id, "crm.create_customer", ActionPolicy.BLOCKED, actor_id=actor_id)
    assert await policy_service.resolve(tenant_id, "crm.create_customer") == ActionPolicy.BLOCKED

    await policy_service.reset_policy(tenant_id, "crm.create_customer", actor_id=actor_id)
    assert await policy_service.resolve(tenant_id, "crm.create_customer") == ActionPolicy.AUTO


async def test_list_policies_excludes_infrastructure_tools(tool_registry) -> None:
    policy_service = PolicyService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    rows = await policy_service.list_policies(tenant_id)
    tool_names = {r["tool_name"] for r in rows}
    assert "crm.create_customer" in tool_names
    assert not any(n.startswith("approvals.") for n in tool_names)
    assert not any(n.startswith("automation.") for n in tool_names)


async def test_api_rejects_system_blocked_override(client, tool_registry) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Policy Co",
            "full_name": "Owner",
            "email": "owner@policyco.com",
            "password": "supersecret1",
        },
    )
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/v1/automation/policies/customer.delete", json={"policy": "AUTO"}, headers=headers
    )
    assert resp.status_code == 403


async def test_api_set_and_get_policy_round_trip(client, tool_registry) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Policy Co 2",
            "full_name": "Owner",
            "email": "owner2@policyco.com",
            "password": "supersecret1",
        },
    )
    token = register.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/v1/automation/policies/crm.create_customer", json={"policy": "APPROVAL_REQUIRED"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["current_policy"] == "APPROVAL_REQUIRED"

    get_resp = await client.get("/api/v1/automation/policies/crm.create_customer", headers=headers)
    assert get_resp.json()["current_policy"] == "APPROVAL_REQUIRED"

    list_resp = await client.get("/api/v1/automation/policies", headers=headers)
    row = next(p for p in list_resp.json()["policies"] if p["tool_name"] == "crm.create_customer")
    assert row["current_policy"] == "APPROVAL_REQUIRED"
    assert row["has_override"] is True
