"""Phase 6 (Agent Runtime Reliability): event trigger -> Agent Runtime
wiring. Wires the previously-modeled-but-unwired `AgentTriggerSource.EVENT`
through the EXISTING event bus only — one generic dispatcher subscribed to
every real `EventType`, structured directly on
`app/events/automation_handlers.py`'s own proven pattern (never a second
event transport, never a competing poller).

Trigger configuration lives on `AgentVersion.triggers["event"]` (same JSON
column and same "no new table" reconciliation as
`agent_trigger_service.py`'s schedule trigger — see that module's
docstring):

    triggers = {
        "event": {
            "enabled": bool,
            "event_type": "<one of app.models.event.EventType's values>",
            "conditions": {"<payload key>": <exact value>, ...},  # optional
            "goal": "<the goal text handed to AgentReasoningService.start>",
        },
        ...
    }

Deterministic filtering, never LLM-decided (mandatory rule): `_matches`
below is a plain, structural equality check over `event.payload` — an
LLM never sees the event, and never decides whether it should trigger an
Agent. The LLM only starts reasoning AFTER this deterministic gate has
already decided an execution should begin.

Event payload is untrusted: this handler NEVER forwards the raw
`event.payload` into the Agent's `goal`/prompt. The goal handed to
`AgentReasoningService.start()` is always the operator-authored static
`triggers["event"]["goal"]` string, with only a small, fixed, bounded
metadata note appended (event_type/entity_type/entity_id — never arbitrary
payload values) — see `_build_goal`. If the agent's own governed tools need
the entity's real data, they look it up themselves (the same pattern every
other governed tool call in this codebase already uses), never by trusting
whatever a webhook/event payload happened to contain. `conditions`
matching (above) is the only place `event.payload` is read at all, and
that match result is a boolean gate, never text that reaches the model.

Idempotency: reuses `AgentExecution.idempotency_key`'s existing unique
constraint — `f"event:{event.id}:{agent.id}"` — so the same event
delivered more than once (the event bus's own documented at-least-once
redelivery/retry behavior) can start at most one logical execution per
(event, agent) pair; `AgentReasoningService.start()`'s existing
`DuplicateExecutionRequestError` on a repeat key is simply caught and
skipped here, exactly like the scheduled-trigger path.

Tenant isolation: every candidate query is scoped to `event.tenant_id`
(the event's own already-verified tenant, sourced by the publisher, never
from anything in this handler) — an Agent belonging to a different tenant
is never even queried, let alone triggered.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.agent import Agent, AgentStatus, AgentTriggerSource, AgentVersion
from app.models.audit_log import AuditLog
from app.models.actor import ActorType
from app.models.event import Event, EventType
from app.services.agent_execution_service import (
    AgentExecutionService,
    AgentNotExecutableError,
    DuplicateExecutionRequestError,
)
from app.services.agent_reasoning_service import AgentReasoningService
from app.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

_MAX_GOAL_CHARS = 4000


def _matches(conditions: dict, payload: dict) -> bool:
    """Deterministic, structural equality only — never an LLM call, never a
    substring/regex/expression language. An empty/missing `conditions`
    matches every delivery of the subscribed `event_type` (the trigger is
    "any event of this type")."""
    if not conditions:
        return True
    for key, expected in conditions.items():
        if payload.get(key) != expected:
            return False
    return True


def _build_goal(base_goal: str, event: Event) -> str:
    """Never interpolates raw event.payload values — only fixed, bounded
    metadata fields. See module docstring's "Event payload is untrusted"."""
    note = (
        f"\n\n(Triggering event: type={event.event_type}, entity_type={event.entity_type}, "
        f"entity_id={event.entity_id}.)"
    )
    return (base_goal or "")[: _MAX_GOAL_CHARS - len(note)] + note


def register_agent_trigger_handlers(
    bus: EventBus, session_factory: async_sessionmaker, tool_registry: ToolRegistry, ai_provider=None,
) -> None:
    exec_service = AgentExecutionService(session_factory, tool_registry)
    reasoning_service = AgentReasoningService(session_factory, tool_registry, exec_service, ai_provider)

    async def agent_event_trigger_dispatch(event: Event) -> None:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Agent, AgentVersion)
                    .join(AgentVersion, Agent.current_version_id == AgentVersion.id)
                    .where(Agent.tenant_id == event.tenant_id)
                )
            ).all()

        matches = []
        for agent, version in rows:
            trigger = (version.triggers or {}).get("event") if isinstance(version.triggers, dict) else None
            if not isinstance(trigger, dict) or not trigger.get("enabled"):
                continue
            if trigger.get("event_type") != event.event_type:
                continue
            if not _matches(trigger.get("conditions") or {}, event.payload or {}):
                continue
            matches.append((agent, version, trigger))
        if not matches:
            return

        for agent, version, trigger in matches:
            if agent.status != AgentStatus.ACTIVE:
                await _audit_skip(session_factory, agent, "agent.trigger.event.skipped", reason=f"agent_status={agent.status}")
                continue

            goal = _build_goal(trigger.get("goal") or version.instructions_snapshot, event)
            idempotency_key = f"event:{event.id}:{agent.id}"
            try:
                execution = await reasoning_service.start(
                    event.tenant_id, agent.id, goal=goal, triggered_by=None,
                    trigger_source=AgentTriggerSource.EVENT, idempotency_key=idempotency_key,
                    correlation_id=event.id,
                )
            except DuplicateExecutionRequestError:
                await _audit_skip(session_factory, agent, "agent.trigger.event.deduplicated", reason=idempotency_key)
                continue
            except AgentNotExecutableError as exc:
                await _audit_skip(session_factory, agent, "agent.trigger.event.skipped", reason=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — one agent's failure must never block others or the event worker
                logger.error("agent_event_trigger_dispatch_failed", agent_id=str(agent.id), event_id=str(event.id), error=str(exc))
                continue

            await _audit_dispatch(session_factory, agent, execution.id, event)

    for event_type in EventType:
        bus.subscribe(event_type, "agent_trigger_dispatch", agent_event_trigger_dispatch)


async def _audit_dispatch(session_factory, agent, execution_id, event) -> None:
    async with session_factory() as session:
        session.add(
            AuditLog(
                tenant_id=agent.tenant_id, actor_type=ActorType.SYSTEM, actor_id=None,
                action="agent.trigger.event", tool=None, entity_type="agent_execution", entity_id=execution_id,
                input_summary={"agent_id": str(agent.id), "event_id": str(event.id), "event_type": event.event_type},
                result="success", source_event_id=event.id, correlation_id=execution_id,
            )
        )
        await session.commit()


async def _audit_skip(session_factory, agent, action, *, reason: str) -> None:
    async with session_factory() as session:
        session.add(
            AuditLog(
                tenant_id=agent.tenant_id, actor_type=ActorType.SYSTEM, actor_id=None,
                action=action, tool=None, entity_type="agent", entity_id=agent.id,
                input_summary={"reason": reason}, result="success",
            )
        )
        await session.commit()
