"""Phase 10A: the single place a tool's effective policy is resolved for a
given tenant. Extends, does not replace, `app/tools/policy.py`'s static
`DEFAULT_TOOL_POLICIES` — that table is still the system default and the
fallback; this service only ever adds a tenant-specific override on top of
it, persisted in `TenantToolPolicy`.

Resolution order (checked at execution time, every time — never cached
across a request):
    1. SYSTEM_BLOCKED_TOOLS always wins — no tenant override can escape it.
    2. A tenant-specific TenantToolPolicy row, if one exists and is enabled.
    3. The static system default (`policy_for`).
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.tool_policy import TenantToolPolicy
from app.tools.policy import (
    DEFAULT_TOOL_POLICIES,
    SYSTEM_BLOCKED_TOOLS,
    ActionPolicy,
    policy_for,
)

logger = structlog.get_logger(__name__)

# Tools whose "policy" is really internal plumbing (reading/approving/
# configuring the platform itself), not a business action an Owner would
# ever think of as "what can Klaros do automatically?" — excluded from
# `list_policies` so /settings/automation shows only real domain actions.
# `resolve()` still applies to every tool, including these; only the admin
# LISTING is filtered.
_INFRASTRUCTURE_PREFIXES = (
    "approvals.",
    "audit.",
    "automation.",
    "events.",
    "notifications.",
    "system.",
    "knowledge.",
)


class SystemBlockedPolicyError(Exception):
    """Raised when a tenant tries to set a policy for a tool the platform
    has permanently blocked. Maps to HTTP 403 at the API layer."""


class PolicyService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def resolve(self, tenant_id: uuid.UUID, tool_name: str) -> ActionPolicy:
        if tool_name in SYSTEM_BLOCKED_TOOLS:
            return ActionPolicy.BLOCKED

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = (
                await session.execute(
                    select(TenantToolPolicy).where(
                        TenantToolPolicy.tenant_id == tenant_id,
                        TenantToolPolicy.tool_name == tool_name,
                    )
                )
            ).scalar_one_or_none()

        if row is not None and row.enabled:
            return ActionPolicy(row.policy)

        return policy_for(tool_name)

    async def list_policies(self, tenant_id: uuid.UUID) -> list[dict]:
        """Every known tool (every key in DEFAULT_TOOL_POLICIES, the
        complete catalog of tools this system has ever assigned a default
        to), merged with this tenant's overrides, if any."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(TenantToolPolicy).where(TenantToolPolicy.tenant_id == tenant_id)
                )
            ).scalars().all()
        overrides = {r.tool_name: r for r in rows}

        results = []
        for tool_name, default_policy in sorted(DEFAULT_TOOL_POLICIES.items()):
            if tool_name.startswith(_INFRASTRUCTURE_PREFIXES):
                continue
            override = overrides.get(tool_name)
            system_blocked = tool_name in SYSTEM_BLOCKED_TOOLS
            effective = (
                ActionPolicy.BLOCKED
                if system_blocked
                else (ActionPolicy(override.policy) if override and override.enabled else default_policy)
            )
            results.append(
                {
                    "tool_name": tool_name,
                    "default_policy": default_policy,
                    "current_policy": effective,
                    "has_override": override is not None and override.enabled,
                    "system_blocked": system_blocked,
                    "configured_by": str(override.configured_by) if override and override.configured_by else None,
                    "updated_at": override.updated_at.isoformat() if override else None,
                    "version": override.version if override else 0,
                }
            )
        return results

    async def get_policy_detail(self, tenant_id: uuid.UUID, tool_name: str) -> dict:
        if tool_name not in DEFAULT_TOOL_POLICIES or tool_name.startswith(_INFRASTRUCTURE_PREFIXES):
            raise ValueError(f"Unknown or non-configurable tool: {tool_name}")
        all_policies = await self.list_policies(tenant_id)
        return next(p for p in all_policies if p["tool_name"] == tool_name)

    async def set_policy(
        self,
        tenant_id: uuid.UUID,
        tool_name: str,
        new_policy: ActionPolicy,
        *,
        actor_id: uuid.UUID | None,
        correlation_id: uuid.UUID | None = None,
    ) -> dict:
        if tool_name not in DEFAULT_TOOL_POLICIES or tool_name.startswith(_INFRASTRUCTURE_PREFIXES):
            raise ValueError(f"Unknown or non-configurable tool: {tool_name}")
        if tool_name in SYSTEM_BLOCKED_TOOLS and new_policy != ActionPolicy.BLOCKED:
            raise SystemBlockedPolicyError(
                f"'{tool_name}' is permanently blocked by platform policy and cannot be changed"
            )

        old_policy = await self.resolve(tenant_id, tool_name)
        now = datetime.now(timezone.utc)

        # The whole select-then-insert-or-CAS-update sequence is retried
        # end-to-end (fresh session each attempt) rather than only the
        # update half — under real concurrent writers, the row that wins an
        # insert race might not be visible yet to a loser still on its own
        # transaction, so re-selecting from scratch is what actually
        # guarantees this converges instead of just the version CAS alone.
        for _ in range(8):
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                existing = (
                    await session.execute(
                        select(TenantToolPolicy).where(
                            TenantToolPolicy.tenant_id == tenant_id,
                            TenantToolPolicy.tool_name == tool_name,
                        )
                    )
                ).scalar_one_or_none()

                if existing is None:
                    row = TenantToolPolicy(
                        tenant_id=tenant_id,
                        tool_name=tool_name,
                        policy=new_policy,
                        enabled=True,
                        configured_by=actor_id,
                        version=1,
                    )
                    session.add(row)
                    try:
                        await session.commit()
                        break
                    except IntegrityError:
                        # Lost a concurrent insert race — another writer's
                        # row now exists; retry from the top and take the
                        # CAS-update path against it instead.
                        await session.rollback()
                        continue

                # DB-level compare-and-swap on `version` — never a Python
                # lock — so two concurrent policy changes for the same tool
                # can't silently clobber each other; the loser's `rowcount`
                # is 0 and it retries against the fresh row.
                cas = await session.execute(
                    update(TenantToolPolicy)
                    .where(
                        TenantToolPolicy.id == existing.id,
                        TenantToolPolicy.version == existing.version,
                    )
                    .values(
                        policy=new_policy,
                        enabled=True,
                        configured_by=actor_id,
                        version=TenantToolPolicy.version + 1,
                        updated_at=now,
                    )
                )
                await session.commit()
                if cas.rowcount == 1:
                    break
        else:
            raise RuntimeError(f"Could not update policy for '{tool_name}' — too much contention")

        await self._audit(
            tenant_id, actor_id, tool_name, old_policy, new_policy, correlation_id=correlation_id
        )
        return await self.get_policy_detail(tenant_id, tool_name)

    async def reset_policy(
        self,
        tenant_id: uuid.UUID,
        tool_name: str,
        *,
        actor_id: uuid.UUID | None,
        correlation_id: uuid.UUID | None = None,
    ) -> dict:
        if tool_name not in DEFAULT_TOOL_POLICIES or tool_name.startswith(_INFRASTRUCTURE_PREFIXES):
            raise ValueError(f"Unknown or non-configurable tool: {tool_name}")

        old_policy = await self.resolve(tenant_id, tool_name)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (
                await session.execute(
                    select(TenantToolPolicy).where(
                        TenantToolPolicy.tenant_id == tenant_id,
                        TenantToolPolicy.tool_name == tool_name,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                await session.delete(existing)
                await session.commit()

        new_policy = await self.resolve(tenant_id, tool_name)
        if old_policy != new_policy:
            await self._audit(
                tenant_id, actor_id, tool_name, old_policy, new_policy, correlation_id=correlation_id, reset=True
            )
        return await self.get_policy_detail(tenant_id, tool_name)

    async def _audit(
        self,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        tool_name: str,
        old_policy: ActionPolicy,
        new_policy: ActionPolicy,
        *,
        correlation_id: uuid.UUID | None,
        reset: bool = False,
    ) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="automation_policy.reset" if reset else "automation_policy.change",
                    tool=None,
                    entity_type="tenant_tool_policy",
                    entity_id=None,
                    input_summary={
                        "tool_name": tool_name,
                        "old_policy": old_policy.value,
                        "new_policy": new_policy.value,
                    },
                    result="success",
                    correlation_id=correlation_id,
                )
            )
            await session.commit()
