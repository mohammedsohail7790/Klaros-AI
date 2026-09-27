"""Phase 4: AgentService — creation, validation, lifecycle transitions,
tenant isolation, versioning, version immutability, tool-permission
grants (deny-by-default, unknown-tool rejection, duplicate prevention)."""

import uuid

import pytest

from app.models.agent import AgentAutonomyTier, AgentStatus, AgentVersionStatus
from app.models.rbac import Role
from app.services.agent_service import (
    AgentNotFoundError,
    AgentService,
    AgentVersionNotFoundError,
    DuplicateToolPermissionError,
    InvalidAgentTransitionError,
    UnknownToolError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def agent_service(tool_registry) -> AgentService:
    from app.db.session import async_session_maker

    return AgentService(async_session_maker)


async def _make_agent(agent_service, tenant_id, **kwargs):
    defaults = dict(
        name="Lead Qualifier", purpose="Qualify inbound leads", autonomy_tier=AgentAutonomyTier.OBSERVE,
        acting_role=Role.MANAGER, created_by=None,
    )
    defaults.update(kwargs)
    return await agent_service.create_agent(tenant_id, **defaults)


async def test_create_agent_is_draft(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    assert agent.status == AgentStatus.DRAFT
    assert agent.tenant_id == tenant_id
    assert agent.acting_role == Role.MANAGER.value


async def test_get_agent_tenant_isolation(agent_service):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_a)
    with pytest.raises(AgentNotFoundError):
        await agent_service.get_agent(tenant_b, agent.id)


async def test_cannot_activate_without_published_version(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    with pytest.raises(InvalidAgentTransitionError):
        await agent_service.activate(tenant_id, agent.id)


async def test_full_lifecycle_transitions(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    version = await agent_service.create_version(tenant_id, agent.id, instructions="Be helpful.", created_by=None)
    published = await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    assert published.status == AgentVersionStatus.PUBLISHED

    active = await agent_service.activate(tenant_id, agent.id)
    assert active.status == AgentStatus.ACTIVE
    assert active.current_version_id == version.id

    paused = await agent_service.pause(tenant_id, agent.id)
    assert paused.status == AgentStatus.PAUSED

    reactivated = await agent_service.activate(tenant_id, agent.id)
    assert reactivated.status == AgentStatus.ACTIVE

    archived = await agent_service.archive(tenant_id, agent.id)
    assert archived.status == AgentStatus.ARCHIVED


async def test_archived_agent_has_no_further_transitions(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    await agent_service.archive(tenant_id, agent.id)
    with pytest.raises(InvalidAgentTransitionError):
        await agent_service.activate(tenant_id, agent.id)
    with pytest.raises(InvalidAgentTransitionError):
        await agent_service.pause(tenant_id, agent.id)


async def test_draft_agent_can_be_edited_active_cannot(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    updated = await agent_service.update_draft_agent(tenant_id, agent.id, name="New Name", updated_by=None)
    assert updated.name == "New Name"

    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    with pytest.raises(InvalidAgentTransitionError):
        await agent_service.update_draft_agent(tenant_id, agent.id, name="Nope", updated_by=None)


async def test_version_numbers_increment_and_are_unique_per_agent(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    v1 = await agent_service.create_version(tenant_id, agent.id, instructions="v1", created_by=None)
    v2 = await agent_service.create_version(tenant_id, agent.id, instructions="v2", created_by=None)
    assert v1.version == 1
    assert v2.version == 2


async def test_publishing_a_new_version_deprecates_the_old_one(agent_service):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    v1 = await agent_service.create_version(tenant_id, agent.id, instructions="v1", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, v1.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    v2 = await agent_service.create_version(tenant_id, agent.id, instructions="v2", created_by=None)
    published_v2 = await agent_service.publish_version(tenant_id, agent.id, v2.id, actor_id=None)
    assert published_v2.status == AgentVersionStatus.PUBLISHED

    reloaded_v1 = await agent_service.get_version(tenant_id, agent.id, v1.id)
    assert reloaded_v1.status == AgentVersionStatus.DEPRECATED

    reloaded_agent = await agent_service.get_agent(tenant_id, agent.id)
    assert reloaded_agent.current_version_id == v2.id


async def test_version_immutability_history_stays_intact(agent_service):
    """Version 1 -> published -> a new Version 2 is created and published;
    Version 1's own snapshot columns must never change (Phase 4's Version
    Immutability requirement)."""
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    v1 = await agent_service.create_version(tenant_id, agent.id, instructions="original instructions", created_by=None)
    published_v1 = await agent_service.publish_version(tenant_id, agent.id, v1.id, actor_id=None)
    assert published_v1.instructions_snapshot == "original instructions"

    await agent_service.activate(tenant_id, agent.id)
    v2 = await agent_service.create_version(tenant_id, agent.id, instructions="new instructions", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, v2.id, actor_id=None)

    reloaded_v1 = await agent_service.get_version(tenant_id, agent.id, v1.id)
    assert reloaded_v1.instructions_snapshot == "original instructions"
    assert reloaded_v1.status == AgentVersionStatus.DEPRECATED

    reloaded_v2 = await agent_service.get_version(tenant_id, agent.id, v2.id)
    assert reloaded_v2.instructions_snapshot == "new instructions"


async def test_publishing_an_already_published_version_is_rejected(agent_service):
    from app.services.agent_service import AgentVersionImmutableError

    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    v1 = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, v1.id, actor_id=None)
    with pytest.raises(AgentVersionImmutableError):
        await agent_service.publish_version(tenant_id, agent.id, v1.id, actor_id=None)


async def test_version_snapshot_captures_grants_at_publish_time(agent_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    published = await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    tool_names = {e["tool_name"] for e in published.tool_permissions_snapshot}
    assert "system.get_tenant_context" in tool_names


# -------------------------------------------------------- Tool permissions

async def test_unknown_tool_is_rejected(agent_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    with pytest.raises(UnknownToolError):
        await agent_service.grant_tool_permission(
            tenant_id, agent.id, tool_name="does.not.exist", tool_registry=tool_registry, created_by=None
        )


async def test_valid_tool_grant_succeeds(agent_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    grant = await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    assert grant.tool_name == "system.get_tenant_context"


async def test_duplicate_tool_grant_is_rejected(agent_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    with pytest.raises(DuplicateToolPermissionError):
        await agent_service.grant_tool_permission(
            tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
        )


async def test_tool_permission_tenant_isolation(agent_service, tool_registry):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_a)
    await agent_service.grant_tool_permission(
        tenant_a, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    with pytest.raises(AgentNotFoundError):
        await agent_service.grant_tool_permission(
            tenant_b, agent.id, tool_name="system.get_current_time", tool_registry=tool_registry, created_by=None
        )
    grants_b = await agent_service.list_tool_permissions(tenant_b, agent.id)
    assert grants_b == []


async def test_revoke_tool_permission_removes_live_grant(agent_service, tool_registry):
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    await agent_service.revoke_tool_permission(tenant_id, agent.id, "system.get_tenant_context", actor_id=None)
    grants = await agent_service.list_tool_permissions(tenant_id, agent.id)
    assert grants == []


async def test_least_privilege_ungranted_tool_absent_from_snapshot(agent_service, tool_registry):
    """An agent granted exactly one tool must never see a second,
    ungranted tool appear in its published snapshot — no automatic
    inheritance of every ToolRegistry tool."""
    tenant_id = uuid.uuid4()
    agent = await _make_agent(agent_service, tenant_id)
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    published = await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    tool_names = {e["tool_name"] for e in published.tool_permissions_snapshot}
    assert tool_names == {"system.get_tenant_context"}
    assert "system.get_current_time" not in tool_names
