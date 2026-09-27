"""Phase 6 (Agent Runtime Reliability): wires the previously-modeled-but-
unwired `AgentTriggerSource.SCHEDULED` to an actual scheduler.

No new scheduler is built. `check_and_dispatch_scheduled` is invoked as
another `on_tick` hook on the existing `EventWorker` (see
app/events/worker.py's `combine_on_tick`), the exact same mechanism
`AutomationService.check_and_dispatch_scheduled` and the Morning Brief
scheduler already piggyback on — this service is structured as closely as
possible to `AutomationService.check_and_dispatch_scheduled` /
`app/services/automation_schedule.py` on purpose, reusing `check_due`
(DAILY/WEEKLY at a tenant-local `HH:MM`, no cron syntax) directly rather
than re-implementing schedule evaluation.

Trigger configuration lives on `AgentVersion.triggers` (JSON) — a column
that has existed since Phase 4 specifically for "schedule cron expression
and/or event-type subscriptions" (KLAROS_FINAL_AGENT_MODEL.md's own
`AgentVersion` spec) but was never interpreted until now. No new table was
added for this (see PHASE_6_IMPLEMENTATION_LOG.md's reconciliation): the
shape this phase defines and enforces is

    triggers = {
        "schedule": {
            "enabled": bool,
            "frequency": "DAILY" | "WEEKLY",
            "time": "HH:MM",            # tenant-local, see automation_schedule.py
            "weekdays": [0..6, ...],    # required only for WEEKLY
            "goal": "<the goal text handed to AgentReasoningService.start>",
        },
        ...
    }

Because a schedule lives on a specific, immutable, PUBLISHED `AgentVersion`
(exactly like `AutomationVersion.trigger_config`), which version's schedule
fires is never ambiguous, and the execution that schedule creates always
binds to that same version (via `Agent.current_version_id`, resolved fresh
at dispatch time — a version published between trigger-creation and
dispatch simply means the *new* version's own `triggers.schedule` is what
gets evaluated on the next tick, which is the correct, expected behavior
for "the agent's current schedule", not a bug).

Idempotency: reuses `AgentExecution`'s existing real DB unique constraint
`uq_agent_executions_tenant_agent_idempotency` — no new table. A
scheduled occurrence's idempotency key is deterministic:
`f"schedule:{agent.id}:{due.occurrence_date.isoformat()}"`. Two overlapping
ticks (or two worker processes) attempting to dispatch the same
tenant-local calendar day's occurrence can never create two
`AgentExecution` rows — `AgentReasoningService.start()` already raises
`DuplicateExecutionRequestError` on a second attempt with the same key,
which this service simply catches and skips (see `_find_by_idempotency_key`
check inside `start()`).

Missed-schedule policy: same as the Automation Engine's own documented
policy (Rule 8 in automation_schedule.py) — **skip, never catch-up**. If
the worker was down past the target time, the next tick that runs still
fires that day's occurrence exactly once (a late tick is not a missed
one), but there is no code path anywhere in this service that walks
backward and fires multiple past-due occurrences — `check_due` only ever
asks "is *today's* occurrence due right now", never "how many occurrences
were missed". This is the safest deterministic behavior (never a burst of
catch-up executions after an outage) and is exercised by
`test_agent_trigger_service.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent import Agent, AgentStatus, AgentTriggerSource, AgentVersion
from app.models.audit_log import AuditLog
from app.models.actor import ActorType
from app.services.agent_execution_service import AgentNotExecutableError, DuplicateExecutionRequestError
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.automation_schedule import check_due

logger = structlog.get_logger(__name__)


class AgentTriggerService:
    def __init__(self, session_factory: async_sessionmaker, reasoning_service: AgentReasoningService) -> None:
        self._session_factory = session_factory
        self._reasoning = reasoning_service

    async def check_and_dispatch_scheduled(
        self, tenant_id: uuid.UUID | None = None, *, now_utc: datetime | None = None,
    ) -> list[uuid.UUID]:
        """Returns the dispatched (non-deduplicated) execution ids, mainly
        for tests. `tenant_id`, when given, scopes dispatch to one tenant
        only. Every Agent with a `current_version_id` is a schedule
        candidate regardless of `Agent.status` — status is checked AFTER
        computing whether the schedule is due, so a due-but-PAUSED/ARCHIVED
        agent is explicitly recorded as skipped (audited), never silently
        ignored and never left ambiguous."""
        from app.models.organization import Organization

        now_utc = now_utc or datetime.now(timezone.utc)
        dispatched: list[uuid.UUID] = []

        async with self._session_factory() as session:
            query = (
                select(Agent, AgentVersion)
                .join(AgentVersion, Agent.current_version_id == AgentVersion.id)
            )
            if tenant_id is not None:
                query = query.where(Agent.tenant_id == tenant_id)
            all_candidates = (await session.execute(query)).all()
            candidates = [
                (agent, version) for agent, version in all_candidates
                if isinstance(version.triggers, dict)
                and isinstance(version.triggers.get("schedule"), dict)
                and version.triggers["schedule"].get("enabled")
            ]
            if not candidates:
                return dispatched

            tenant_ids = {agent.tenant_id for agent, _ in candidates}
            orgs = (await session.execute(select(Organization).where(Organization.id.in_(tenant_ids)))).scalars().all()
            org_timezone = {org.id: org.timezone for org in orgs}

        for agent, version in candidates:
            schedule = version.triggers["schedule"]
            tz_name = org_timezone.get(agent.tenant_id, "UTC")
            try:
                due = check_due(schedule, tz_name, now_utc=now_utc)
            except Exception as exc:  # noqa: BLE001 — one agent's bad schedule config must never block others
                logger.error("agent_schedule_invalid_config", agent_id=str(agent.id), error=str(exc))
                continue
            if not due.is_due:
                continue

            if agent.status != AgentStatus.ACTIVE:
                await self._audit_skip(
                    agent, "agent.trigger.scheduled.skipped", reason=f"agent_status={agent.status}"
                )
                continue

            idempotency_key = f"schedule:{agent.id}:{due.occurrence_date.isoformat()}"
            goal = schedule.get("goal") or version.instructions_snapshot

            try:
                execution = await self._reasoning.start(
                    agent.tenant_id, agent.id, goal=goal, triggered_by=None,
                    trigger_source=AgentTriggerSource.SCHEDULED, idempotency_key=idempotency_key,
                )
            except DuplicateExecutionRequestError:
                await self._audit_skip(agent, "agent.trigger.scheduled.deduplicated", reason=idempotency_key)
                continue
            except AgentNotExecutableError as exc:
                await self._audit_skip(agent, "agent.trigger.scheduled.skipped", reason=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — one agent's failure must never block others or the worker tick
                logger.error("agent_schedule_dispatch_failed", agent_id=str(agent.id), error=str(exc))
                continue

            await self._audit_dispatch(agent, execution.id)
            dispatched.append(execution.id)

        return dispatched

    async def _audit_dispatch(self, agent: Agent, execution_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            session.add(
                AuditLog(
                    tenant_id=agent.tenant_id, actor_type=ActorType.SYSTEM, actor_id=None,
                    action="agent.trigger.scheduled", tool=None, entity_type="agent_execution",
                    entity_id=execution_id, input_summary={"agent_id": str(agent.id)}, result="success",
                    correlation_id=execution_id,
                )
            )
            await session.commit()

    async def _audit_skip(self, agent: Agent, action: str, *, reason: str) -> None:
        async with self._session_factory() as session:
            session.add(
                AuditLog(
                    tenant_id=agent.tenant_id, actor_type=ActorType.SYSTEM, actor_id=None,
                    action=action, tool=None, entity_type="agent", entity_id=agent.id,
                    input_summary={"reason": reason}, result="success",
                )
            )
            await session.commit()
