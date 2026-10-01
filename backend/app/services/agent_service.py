"""Phase 4 (Agent Runtime foundation): CRUD, lifecycle, versioning, and
tool-permission management for `Agent`/`AgentVersion`/`AgentToolPermission`.

Deliberately does NOT execute anything — see agent_execution_service.py for
the governed execution path. This module only manages the durable
configuration an execution later runs against, matching the explicit
"Recommendation -> Human decision -> Agent configuration -> Agent" boundary
this phase's instructions require (accepting a Recommendation, or any other
input, never automatically creates or runs an Agent — every mutation here
is triggered by an explicit human-initiated API call).

Every tool referenced by `grant_tool_permission` is validated against the
live `ToolRegistry` at grant time — never persisted as valid without that
check (deny-by-default, unknown tools rejected).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.agent import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentToolPermission,
    AgentVersion,
    AgentVersionStatus,
)
from app.models.audit_log import AuditLog
from app.models.event import EventType
from app.models.rbac import Role
from app.services.automation_schedule import InvalidScheduleError, validate_schedule_config
from app.tools.registry import ToolRegistry


def _validate_triggers(triggers: dict) -> None:
    """Phase 6: structural validation only, run at version-creation time —
    never arbitrary Python expressions, never a cron string (see
    app/services/agent_trigger_service.py's module docstring for the exact
    schema this enforces). Reuses the Automation Engine's own
    `validate_schedule_config` for the `schedule` sub-object rather than a
    second implementation, and restricts `event.event_type` to a real,
    known `EventType` value (never an arbitrary tenant-supplied string) so
    the deterministic event-trigger matcher can never be pointed at
    something that will silently never fire."""
    if not isinstance(triggers, dict):
        raise InvalidTriggerConfigError("triggers must be an object")

    schedule = triggers.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, dict):
            raise InvalidTriggerConfigError("triggers.schedule must be an object")
        if "enabled" not in schedule or not isinstance(schedule.get("enabled"), bool):
            raise InvalidTriggerConfigError("triggers.schedule.enabled (bool) is required")
        if schedule.get("enabled"):
            try:
                validate_schedule_config(schedule)
            except InvalidScheduleError as exc:
                raise InvalidTriggerConfigError(str(exc)) from exc
            if not schedule.get("goal"):
                raise InvalidTriggerConfigError("triggers.schedule.goal is required when enabled")

    event = triggers.get("event")
    if event is not None:
        if not isinstance(event, dict):
            raise InvalidTriggerConfigError("triggers.event must be an object")
        if "enabled" not in event or not isinstance(event.get("enabled"), bool):
            raise InvalidTriggerConfigError("triggers.event.enabled (bool) is required")
        if event.get("enabled"):
            event_type = event.get("event_type")
            if event_type not in {e.value for e in EventType}:
                raise InvalidTriggerConfigError(f"triggers.event.event_type must be a known EventType, got {event_type!r}")
            conditions = event.get("conditions")
            if conditions is not None and not isinstance(conditions, dict):
                raise InvalidTriggerConfigError("triggers.event.conditions must be an object")
            if not event.get("goal"):
                raise InvalidTriggerConfigError("triggers.event.goal is required when enabled")


class AgentNotFoundError(Exception):
    pass


class AgentVersionNotFoundError(Exception):
    pass


class InvalidAgentTransitionError(Exception):
    pass


class InvalidTriggerConfigError(Exception):
    """Phase 6: `AgentVersion.triggers["schedule"|"event"]` failed
    structural validation — see `_validate_triggers` below."""


class AgentVersionImmutableError(Exception):
    """Raised on any attempt to mutate a version that has already been
    referenced by an execution, or that is no longer DRAFT."""


class UnknownToolError(Exception):
    pass


class DuplicateToolPermissionError(Exception):
    pass


_VALID_TRANSITIONS: dict[str, set[str]] = {
    AgentStatus.DRAFT: {AgentStatus.ACTIVE, AgentStatus.ARCHIVED},
    AgentStatus.ACTIVE: {AgentStatus.PAUSED, AgentStatus.ARCHIVED},
    AgentStatus.PAUSED: {AgentStatus.ACTIVE, AgentStatus.ARCHIVED},
    AgentStatus.ARCHIVED: set(),
}


class AgentService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ---------------------------------------------------------------- Agent

    async def create_agent(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        purpose: str,
        autonomy_tier: str,
        acting_role: Role,
        created_by: uuid.UUID | None,
        source_blueprint_id: uuid.UUID | None = None,
        source_blueprint_version: int | None = None,
        source_recommendation_id: uuid.UUID | None = None,
    ) -> Agent:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = Agent(
                tenant_id=tenant_id,
                name=name,
                purpose=purpose,
                status=AgentStatus.DRAFT,
                autonomy_tier=autonomy_tier,
                acting_role=acting_role.value if isinstance(acting_role, Role) else acting_role,
                created_by=created_by,
                source_blueprint_id=source_blueprint_id,
                source_blueprint_version=source_blueprint_version,
                source_recommendation_id=source_recommendation_id,
            )
            session.add(agent)
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=created_by,
                    action="agent.create", tool=None, entity_type="agent", entity_id=agent.id,
                    input_summary={"name": name, "autonomy_tier": autonomy_tier}, result="success",
                )
            )
            await session.commit()
            await session.refresh(agent)
            return _detached(agent)

    async def get_agent(self, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")
            return _detached(agent)

    async def list_agents(self, tenant_id: uuid.UUID, *, status: str | None = None) -> list[Agent]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(Agent).where(Agent.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(Agent.status == status)
            rows = (await session.execute(stmt.order_by(Agent.created_at.desc()))).scalars().all()
            return [_detached(r) for r in rows]

    async def update_draft_agent(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        name: str | None = None,
        purpose: str | None = None,
        autonomy_tier: str | None = None,
        updated_by: uuid.UUID | None,
    ) -> Agent:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")
            if agent.status != AgentStatus.DRAFT:
                raise InvalidAgentTransitionError(
                    "Only a DRAFT agent may be edited directly — publish a new version instead"
                )
            if name is not None:
                agent.name = name
            if purpose is not None:
                agent.purpose = purpose
            if autonomy_tier is not None:
                agent.autonomy_tier = autonomy_tier
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=updated_by,
                    action="agent.update", tool=None, entity_type="agent", entity_id=agent.id,
                    input_summary={"name": name, "autonomy_tier": autonomy_tier}, result="success",
                )
            )
            await session.commit()
            await session.refresh(agent)
            return _detached(agent)

    async def _transition(
        self, tenant_id: uuid.UUID, agent_id: uuid.UUID, *, to_status: str, actor_id: uuid.UUID | None
    ) -> Agent:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")
            allowed = _VALID_TRANSITIONS.get(agent.status, set())
            if to_status not in allowed:
                raise InvalidAgentTransitionError(f"Cannot transition agent from {agent.status} to {to_status}")
            if to_status == AgentStatus.ACTIVE and agent.current_version_id is None:
                raise InvalidAgentTransitionError("Agent has no published version to activate")
            agent.status = to_status
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id,
                    action=f"agent.{to_status.lower()}", tool=None, entity_type="agent", entity_id=agent.id,
                    input_summary={}, result="success",
                )
            )
            await session.commit()
            await session.refresh(agent)
            return _detached(agent)

    async def activate(self, tenant_id, agent_id, *, actor_id=None) -> Agent:
        return await self._transition(tenant_id, agent_id, to_status=AgentStatus.ACTIVE, actor_id=actor_id)

    async def pause(self, tenant_id, agent_id, *, actor_id=None) -> Agent:
        return await self._transition(tenant_id, agent_id, to_status=AgentStatus.PAUSED, actor_id=actor_id)

    async def archive(self, tenant_id, agent_id, *, actor_id=None) -> Agent:
        return await self._transition(tenant_id, agent_id, to_status=AgentStatus.ARCHIVED, actor_id=actor_id)

    # ---------------------------------------------------------- Tool grants

    async def grant_tool_permission(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        tool_name: str,
        tool_registry: ToolRegistry,
        constraint_config: dict | None = None,
        created_by: uuid.UUID | None,
    ) -> AgentToolPermission:
        # Deny-by-default enforcement point: the tool must exist in the
        # LIVE registry right now — never trusted as a bare string.
        try:
            tool_registry.get(tool_name)
        except Exception as exc:  # ToolNotFoundError
            raise UnknownToolError(f"Unknown tool: {tool_name}") from exc

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")

            existing = (
                await session.execute(
                    select(AgentToolPermission).where(
                        AgentToolPermission.agent_id == agent_id,
                        AgentToolPermission.tool_name == tool_name,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise DuplicateToolPermissionError(f"Tool '{tool_name}' is already granted to this agent")

            grant = AgentToolPermission(
                tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name,
                constraint_config=constraint_config, created_by=created_by,
            )
            session.add(grant)
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=created_by,
                    action="agent.tool_permission.create", tool=tool_name, entity_type="agent", entity_id=agent_id,
                    input_summary={"tool_name": tool_name}, result="success",
                )
            )
            await session.commit()
            await session.refresh(grant)
            return _detached(grant)

    async def revoke_tool_permission(
        self, tenant_id: uuid.UUID, agent_id: uuid.UUID, tool_name: str, *, actor_id: uuid.UUID | None
    ) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")
            grant = (
                await session.execute(
                    select(AgentToolPermission).where(
                        AgentToolPermission.agent_id == agent_id,
                        AgentToolPermission.tool_name == tool_name,
                    )
                )
            ).scalar_one_or_none()
            if grant is None:
                return
            await session.delete(grant)
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id,
                    action="agent.tool_permission.remove", tool=tool_name, entity_type="agent", entity_id=agent_id,
                    input_summary={"tool_name": tool_name}, result="success",
                )
            )
            await session.commit()

    async def list_tool_permissions(self, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> list[AgentToolPermission]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(AgentToolPermission).where(
                        AgentToolPermission.tenant_id == tenant_id, AgentToolPermission.agent_id == agent_id
                    )
                )
            ).scalars().all()
            return [_detached(r) for r in rows]

    # -------------------------------------------------------------- Version

    async def create_version(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        instructions: str,
        memory_refs: list | None = None,
        triggers: dict | None = None,
        max_executions_per_hour: int = 20,
        max_concurrent_executions: int = 1,
        max_tool_chain_depth: int = 1,
        approval_policy_override: str | None = None,
        created_by: uuid.UUID | None,
    ) -> AgentVersion:
        """Creates a new DRAFT version, snapshotting the agent's CURRENT
        live `AgentToolPermission` grants into `tool_permissions_snapshot`
        at creation time (re-snapshotted again, idempotently, at publish —
        see `publish_version` — so a grant added between draft-creation and
        publish is still captured, matching "no automatic inheritance of
        every ToolRegistry tool" while still reflecting deliberate grants
        made before publish)."""
        _validate_triggers(triggers or {})
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")

            max_version = (
                await session.execute(
                    select(AgentVersion.version)
                    .where(AgentVersion.agent_id == agent_id)
                    .order_by(AgentVersion.version.desc())
                )
            ).scalars().first()
            next_version = (max_version or 0) + 1

            grants = (
                await session.execute(
                    select(AgentToolPermission).where(AgentToolPermission.agent_id == agent_id)
                )
            ).scalars().all()
            snapshot = [{"tool_name": g.tool_name, "constraint": g.constraint_config} for g in grants]

            version = AgentVersion(
                tenant_id=tenant_id, agent_id=agent_id, version=next_version, status=AgentVersionStatus.DRAFT,
                instructions_snapshot=instructions, tool_permissions_snapshot=snapshot,
                memory_refs=memory_refs or [], triggers=triggers or {},
                max_executions_per_hour=max_executions_per_hour,
                max_concurrent_executions=max_concurrent_executions,
                max_tool_chain_depth=max_tool_chain_depth,
                approval_policy_override=approval_policy_override, created_by=created_by,
            )
            session.add(version)
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=created_by,
                    action="agent.version.create", tool=None, entity_type="agent_version", entity_id=version.id,
                    input_summary={"agent_id": str(agent_id), "version": next_version}, result="success",
                )
            )
            await session.commit()
            await session.refresh(version)
            return _detached(version)

    async def publish_version(
        self, tenant_id: uuid.UUID, agent_id: uuid.UUID, version_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> AgentVersion:
        """Publishing re-snapshots live grants one final time (see
        create_version's docstring), sets status=PUBLISHED, deprecates any
        prior PUBLISHED version for this agent, and makes this the agent's
        `current_version_id`. Never mutates an already-PUBLISHED/
        DEPRECATED version's executable columns — Version Immutability."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotFoundError("Agent not found")
            version = await session.get(AgentVersion, version_id)
            if version is None or version.tenant_id != tenant_id or version.agent_id != agent_id:
                raise AgentVersionNotFoundError("Agent version not found")
            if version.status != AgentVersionStatus.DRAFT:
                raise AgentVersionImmutableError(f"Version is not DRAFT (status={version.status})")

            grants = (
                await session.execute(
                    select(AgentToolPermission).where(AgentToolPermission.agent_id == agent_id)
                )
            ).scalars().all()
            version.tool_permissions_snapshot = [
                {"tool_name": g.tool_name, "constraint": g.constraint_config} for g in grants
            ]
            version.status = AgentVersionStatus.PUBLISHED

            await session.execute(
                update(AgentVersion)
                .where(
                    AgentVersion.agent_id == agent_id,
                    AgentVersion.id != version.id,
                    AgentVersion.status == AgentVersionStatus.PUBLISHED,
                )
                .values(status=AgentVersionStatus.DEPRECATED)
            )
            agent.current_version_id = version.id

            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id,
                    action="agent.version.publish", tool=None, entity_type="agent_version", entity_id=version.id,
                    input_summary={"agent_id": str(agent_id), "version": version.version}, result="success",
                )
            )
            await session.commit()
            await session.refresh(version)
            return _detached(version)

    async def get_version(self, tenant_id: uuid.UUID, agent_id: uuid.UUID, version_id: uuid.UUID) -> AgentVersion:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            version = await session.get(AgentVersion, version_id)
            if version is None or version.tenant_id != tenant_id or version.agent_id != agent_id:
                raise AgentVersionNotFoundError("Agent version not found")
            return _detached(version)

    async def list_versions(self, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> list[AgentVersion]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(AgentVersion)
                    .where(AgentVersion.tenant_id == tenant_id, AgentVersion.agent_id == agent_id)
                    .order_by(AgentVersion.version.desc())
                )
            ).scalars().all()
            return [_detached(r) for r in rows]

    # ------------------------------------------------------------- History

    async def list_executions(
        self, tenant_id: uuid.UUID, agent_id: uuid.UUID, *, limit: int = 50
    ) -> list[AgentExecution]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(AgentExecution)
                    .where(AgentExecution.tenant_id == tenant_id, AgentExecution.agent_id == agent_id)
                    .order_by(AgentExecution.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [_detached(r) for r in rows]


def _detached(obj):
    """Every SQLAlchemy ORM row this service returns has already been
    committed/refreshed; the caller may read its columns after the
    session closes (same pattern as recommendation_service.py /
    business_blueprint_service.py throughout this codebase)."""
    return obj
