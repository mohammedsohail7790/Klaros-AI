"""Phase 6 (Agent Runtime Reliability): security/governance-preservation
tests for crash recovery — version immutability, permission-snapshot
immutability, and kill-switch enforcement must all hold identically for a
RECOVERED execution as they already do for a normal one (Phase 4/5
guarantees, re-proven here under the recovery path). Also covers the
lease-ownership logic (heartbeat never extends another owner's lease) at
the single-process/logic level; the genuine concurrent-race version lives
in test_postgres_agent_recovery.py (SQLite gives no real row-locking
guarantee, only the correct WHERE-clause logic, which is still worth
proving deterministically here).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.agent import (
    AgentAutonomyTier,
    AgentExecutionMode,
    AgentExecutionStatus,
    AgentExecutionTerminationReason,
)
from app.models.organization import Organization
from app.models.rbac import Role
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_recovery_service import AgentRecoveryService
from app.services.agent_service import AgentService
from app.services.ai_provider import AICallOutcome, AIProvider

pytestmark = pytest.mark.asyncio


class FakeAIProvider(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        item = self._responses.pop(0)
        text = item if isinstance(item, str) else json.dumps(item)
        return AICallOutcome(success=True, provider=self.name, model=self.model, latency_ms=1, raw_text=text)


def _decision(action, **kwargs):
    d = {"action": action, "reasoning_summary": "r", "arguments": {}}
    d.update(kwargs)
    return d


def _reasoning(tool_registry, responses):
    from app.db.session import async_session_maker

    exec_service = AgentExecutionService(async_session_maker, tool_registry)
    return AgentReasoningService(async_session_maker, tool_registry, exec_service, ai_provider=FakeAIProvider(responses))


async def _running_execution(tenant_id, agent_id, version_id, *, step_count=0):
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent_id, agent_version_id=version_id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g",
            reasoning_state={"goal": "g", "history": []}, step_count=step_count,
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        return execution.id


async def test_version_immutability_preserved_during_recovery(tool_registry):
    """Execution starts on Version 1, "crashes" (simulated via lease
    backdating), Version 2 is published, recovery occurs — the resumed
    loop must never silently switch to executing under the new current
    version. Note (a real, pre-existing Phase 4 interaction, re-proven
    here rather than assumed): `ToolRegistry._check_agent_tool_permission`
    already refuses to execute ANY tool call for an `AgentExecution` whose
    `agent_version_id` is no longer the agent's current published version
    (`agent.current_version_id != version.id`) — this is true for ANY
    in-flight execution, not something Phase 6 introduces. So the CORRECT,
    stronger proof of "never silently switch versions" is: the resumed
    execution's `agent_version_id` stays v1 (never rewritten to v2) AND
    its next tool call is cleanly governed-rejected rather than silently
    permitted under v1's now-stale authority or silently upgraded to v2's
    tools/instructions."""
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="VersionProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    v1 = await agent_service.create_version(
        tenant_id, agent.id, instructions="VERSION ONE INSTRUCTIONS", max_tool_chain_depth=3, created_by=None
    )
    await agent_service.publish_version(tenant_id, agent.id, v1.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)

    execution_id = await _running_execution(tenant_id, agent.id, v1.id)

    # A second tool is granted and a Version 2 published AFTER the
    # execution started — the execution must never see it.
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_current_time", tool_registry=tool_registry, created_by=None
    )
    v2 = await agent_service.create_version(
        tenant_id, agent.id, instructions="VERSION TWO INSTRUCTIONS (must never be used)",
        max_tool_chain_depth=3, created_by=None,
    )
    await agent_service.publish_version(tenant_id, agent.id, v2.id, actor_id=None)

    rs = _reasoning(tool_registry, [
        _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
        _decision("COMPLETE", final_response="done under v1"),
    ])
    recovery = AgentRecoveryService(async_session_maker, rs)
    claimed = await recovery.sweep_once(tenant_id)
    assert execution_id in claimed

    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.agent_version_id == v1.id  # never silently switched to v2
        # Cleanly rejected (v1 is no longer the current version), never
        # silently completed under v2's authority/instructions.
        assert execution.status == AgentExecutionStatus.FAILED
        assert execution.termination_reason == AgentExecutionTerminationReason.TOOL_GOVERNANCE_REJECTED


async def test_live_permission_revocation_does_not_affect_recovered_execution(tool_registry):
    """The live AgentToolPermission grant is revoked BEFORE recovery
    resumes — the resumed loop must still authorize the call because it
    reads the immutable AgentVersion.tool_permissions_snapshot, not the
    live table (Phase 4 semantics, now re-proven under the recovery
    path)."""
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="PermSnapshotProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x", max_tool_chain_depth=3, created_by=None
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)

    execution_id = await _running_execution(tenant_id, agent.id, version.id)

    # Revoke the LIVE grant after the execution started.
    await agent_service.revoke_tool_permission(tenant_id, agent.id, "system.get_tenant_context", actor_id=None)

    rs = _reasoning(tool_registry, [
        _decision("TOOL_CALL", tool_name="system.get_tenant_context"),
        _decision("COMPLETE", final_response="still authorized via snapshot"),
    ])
    recovery = AgentRecoveryService(async_session_maker, rs)
    await recovery.sweep_once(tenant_id)

    from app.models.agent import AgentExecution

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.COMPLETED  # not TOOL_GOVERNANCE_REJECTED


async def test_kill_switch_blocks_next_tool_call_after_recovery(tool_registry):
    """Kill switch (Organization.ai_paused) is set to True BEFORE recovery
    runs — the resumed loop's next tool call must be denied exactly as it
    would for any other in-flight execution (no special exemption for
    recovery)."""
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name="KillSwitchRecoveryCo", slug=f"ks-{tenant_id.hex[:8]}", ai_paused=False))
        await session.commit()

    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="KillSwitchProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(
        tenant_id, agent.id, instructions="x", max_tool_chain_depth=3, created_by=None
    )
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)

    execution_id = await _running_execution(tenant_id, agent.id, version.id)

    # Flip the kill switch after the execution started, before recovery.
    async with async_session_maker() as session:
        org = await session.get(Organization, tenant_id)
        org.ai_paused = True
        await session.commit()

    rs = _reasoning(tool_registry, [_decision("TOOL_CALL", tool_name="system.get_tenant_context")])
    recovery = AgentRecoveryService(async_session_maker, rs)
    await recovery.sweep_once(tenant_id)

    from app.models.agent import AgentExecution, AgentExecutionTerminationReason

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        assert execution.status == AgentExecutionStatus.FAILED
        assert execution.termination_reason == AgentExecutionTerminationReason.TOOL_GOVERNANCE_REJECTED


async def test_heartbeat_never_extends_a_lease_owned_by_a_different_worker(tool_registry):
    """Owner A's lease exists; a DIFFERENT AgentReasoningService instance
    (owner B, its own worker_id) attempts to renew it via `_renew_lease` —
    must be a no-op. Proves the WHERE execution_owner_id=<self> guard, the
    deterministic half of the "heartbeat can't steal a lease" requirement
    (the genuinely concurrent race is in test_postgres_agent_recovery.py)."""
    from app.db.session import async_session_maker
    from app.models.agent import AgentExecution

    tenant_id = uuid.uuid4()
    agent_service = AgentService(async_session_maker)
    agent = await agent_service.create_agent(
        tenant_id, name="HeartbeatProbe", purpose="x", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    agent = await agent_service.activate(tenant_id, agent.id)

    owner_a = uuid.uuid4()
    original_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    async with async_session_maker() as session:
        execution = AgentExecution(
            tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
            status=AgentExecutionStatus.RUNNING, mode=AgentExecutionMode.REASONING,
            tool_name=None, tool_input_summary={}, goal="g",
            execution_owner_id=owner_a, lease_expires_at=original_expiry,
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    owner_b_reasoning = _reasoning(tool_registry, [])
    owner_b_reasoning._worker_id = uuid.uuid4()  # deliberately NOT owner_a
    await owner_b_reasoning._renew_lease(execution_id)

    async with async_session_maker() as session:
        execution = await session.get(AgentExecution, execution_id)
        # Untouched — owner B's renewal attempt must not have extended it.
        # (SQLite round-trips DateTime as naive, so compare wall-clock
        # value only, not tzinfo, for this SQLite-backed logic test.)
        assert execution.execution_owner_id == owner_a
        assert execution.lease_expires_at.replace(tzinfo=timezone.utc) == original_expiry
