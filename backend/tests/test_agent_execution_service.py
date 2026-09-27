"""Phase 4: AgentExecutionService — the governed execution path. Proves the
full chain (tenant -> active agent -> active version -> tool permission ->
autonomy ceiling -> existing ToolRegistry checks -> execution -> audit) end
to end, for every autonomy tier, for approval integration, and for tenant
isolation / paused / archived / wrong-version rejection."""

import uuid

import pytest

from app.models.agent import AgentAutonomyTier, AgentExecutionStatus
from app.models.approval import ApprovalStatus
from app.models.rbac import Role
from app.services.agent_execution_service import (
    AgentExecutionService,
    AgentNotExecutableError,
    DuplicateExecutionRequestError,
)
from app.services.agent_service import AgentService
from app.services.approval_execution_service import ApprovalExecutionService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def agent_service(tool_registry) -> AgentService:
    from app.db.session import async_session_maker

    return AgentService(async_session_maker)


@pytest.fixture
def execution_service(tool_registry) -> AgentExecutionService:
    from app.db.session import async_session_maker

    return AgentExecutionService(async_session_maker, tool_registry)


@pytest.fixture
def approval_execution_service(tool_registry, event_bus):
    from app.db.session import async_session_maker

    return ApprovalExecutionService(async_session_maker, tool_registry, event_bus)


async def _active_agent(
    agent_service, tool_registry, tenant_id, *, tool_name="system.get_tenant_context",
    autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
):
    agent = await agent_service.create_agent(
        tenant_id, name="Runner", purpose="test", autonomy_tier=autonomy_tier,
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name=tool_name, tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)
    return agent, version


# --------------------------------------------------------------- Happy path

async def test_successful_execution_runs_exactly_once(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)

    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.COMPLETED
    assert execution.agent_version_id == version.id
    assert execution.result_summary["tenant_id"] == str(tenant_id)

    history = await agent_service.list_executions(tenant_id, agent.id)
    assert len(history) == 1


async def test_execution_records_exact_agent_and_version(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, version = await _active_agent(agent_service, tool_registry, tenant_id)
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.agent_id == agent.id
    assert execution.agent_version_id == version.id


# ---------------------------------------------------------- Guard rejection

async def test_paused_agent_never_executes(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id)
    await agent_service.pause(tenant_id, agent.id)
    with pytest.raises(AgentNotExecutableError):
        await execution_service.run_action(
            tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
        )


async def test_archived_agent_never_executes(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id)
    await agent_service.pause(tenant_id, agent.id)
    await agent_service.archive(tenant_id, agent.id)
    with pytest.raises(AgentNotExecutableError):
        await execution_service.run_action(
            tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
        )


async def test_draft_agent_never_executes(agent_service, execution_service):
    tenant_id = uuid.uuid4()
    agent = await agent_service.create_agent(
        tenant_id, name="x", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    with pytest.raises(AgentNotExecutableError):
        await execution_service.run_action(
            tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
        )


async def test_wrong_tenant_cannot_execute_agent(agent_service, execution_service, tool_registry):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_a)
    with pytest.raises(AgentNotExecutableError):
        await execution_service.run_action(
            tenant_b, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
        )


async def test_ungranted_tool_is_rejected_at_execution_time(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id, tool_name="system.get_tenant_context")
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_current_time", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.FAILED
    assert "not granted" in execution.error_message


async def test_only_current_version_may_execute(agent_service, execution_service, tool_registry):
    """A version that gets deprecated by a newer publish must never be
    usable for a new execution — only the agent's live current_version_id
    is executable."""
    tenant_id = uuid.uuid4()
    agent, v1 = await _active_agent(agent_service, tool_registry, tenant_id)
    v2 = await agent_service.create_version(tenant_id, agent.id, instructions="v2", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, v2.id, actor_id=None)

    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.agent_version_id == v2.id
    assert execution.agent_version_id != v1.id


# ------------------------------------------------------------------ Idempotency

async def test_duplicate_idempotency_key_does_not_double_execute(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(agent_service, tool_registry, tenant_id)
    first = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
        idempotency_key="req-1",
    )
    with pytest.raises(DuplicateExecutionRequestError) as exc_info:
        await execution_service.run_action(
            tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
            idempotency_key="req-1",
        )
    assert exc_info.value.existing_execution_id == first.id
    history = await agent_service.list_executions(tenant_id, agent.id)
    assert len(history) == 1


# -------------------------------------------------------------------- Autonomy

async def test_observe_tier_never_executes(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.OBSERVE
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.FAILED
    assert "blocked" in execution.error_message.lower()


async def test_recommend_tier_never_executes(agent_service, execution_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.RECOMMEND
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.FAILED


async def test_execute_with_approval_tier_forces_approval_even_for_auto_tool(
    agent_service, execution_service, tool_registry
):
    """system.get_tenant_context is AUTO at the tool-policy layer, but the
    EXECUTE_WITH_APPROVAL tier ceiling must still force an approval —
    proving the tier composes with, and narrows, the tool's own policy."""
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.EXECUTE_WITH_APPROVAL
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.WAITING_APPROVAL
    assert execution.approval_request_id is not None


async def test_execute_autonomous_tier_runs_auto_tool_without_approval(
    agent_service, execution_service, tool_registry
):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.COMPLETED


# ------------------------------------------------------------------- Approval

async def test_approved_execution_resumes_and_completes(
    agent_service, execution_service, approval_execution_service, tool_registry
):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.EXECUTE_WITH_APPROVAL
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.status == AgentExecutionStatus.WAITING_APPROVAL

    approved = await approval_execution_service.approve(
        tenant_id, execution.approval_request_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER,
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.execution_status == "EXECUTED"

    completed = await execution_service.get_execution(tenant_id, execution.id)
    assert completed.status == AgentExecutionStatus.COMPLETED


async def test_rejected_approval_halts_execution_permanently(
    agent_service, execution_service, approval_execution_service, tool_registry
):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.EXECUTE_WITH_APPROVAL
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    await approval_execution_service.reject(tenant_id, execution.approval_request_id, decided_by_id=uuid.uuid4())

    halted = await execution_service.get_execution(tenant_id, execution.id)
    assert halted.status == AgentExecutionStatus.HALTED
    assert halted.error_message is not None


async def test_repeated_approval_callback_does_not_duplicate_side_effect(
    agent_service, execution_service, approval_execution_service, tool_registry
):
    tenant_id = uuid.uuid4()
    agent, _ = await _active_agent(
        agent_service, tool_registry, tenant_id, autonomy_tier=AgentAutonomyTier.EXECUTE_WITH_APPROVAL
    )
    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    approval_id = execution.approval_request_id
    await approval_execution_service.approve(
        tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER,
    )
    # A second resume attempt (e.g. a retried webhook/callback) must be a
    # no-op, not a second tool execution — enforced by the CAS in
    # ApprovalExecutionService.execute_approved, unchanged by this phase.
    again = await approval_execution_service.execute_approved(tenant_id, approval_id)
    assert again.execution_status == "EXECUTED"

    history = await agent_service.list_executions(tenant_id, agent.id)
    assert len(history) == 1
