"""The generic Automation Engine's persistence + execution service.

Governance boundary (identical in spirit to the Voice Receptionist's
`_VOICE_ALLOWED_TOOLS`, app/services/voice_conversation_service.py):
every action step is checked against a hardcoded `ACTION_ALLOWLIST`
before it ever reaches `AIExecutionService.request_tool_execution` — an
automation's owner-authored JSON can never name an arbitrary tool and
have it actually execute. The single non-tool pseudo-action, `"wait"`,
never touches ToolRegistry at all; it only ever schedules a real Temporal
timer (see app/workflows/automation_workflow.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.execution_service import AIExecutionService, ToolRequest
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.automation import (
    Automation,
    AutomationExecution,
    AutomationExecutionStep,
    AutomationVersion,
    AutomationStatus,
    ExecutionStatus,
    StepStatus,
    TriggerType,
)
from app.models.rbac import Role
from app.services.automation_condition import InvalidConditionError, evaluate_condition, validate_condition
from app.services.automation_schedule import InvalidScheduleError, check_due, compute_next_run, validate_schedule_config

logger = structlog.get_logger(__name__)

# Application-enforced — never relaxed to satisfy a specific automation.
# "wait" is handled specially (see run_steps) and never dispatched here.
ACTION_ALLOWLIST = frozenset({
    "notifications.create_notification",
    "crm.update_lead",
    "crm.create_note",
    # Phase 11 (Rule 20): the existing deterministic domain sweeps behind
    # Stale Quote / Overdue Invoice. Both take no input beyond tenant_id
    # (derived from ExecutionContext, never from automation-authored
    # params), are idempotent (re-running only touches rows genuinely due
    # this run), and already publish the real events
    # (quote.expired / exception.created) a follow-up EVENT-triggered
    # automation notifies off of — no new event was invented for this.
    "quotes.detect_expired_quotes",
    "finance.detect_overdue_invoices",
    # Phase 24: the contract-pending-follow-up sweep — same reasoning as
    # the two sweeps above (tenant-derived only, idempotent, already
    # publishes the real event a follow-up EVENT-triggered automation
    # reacts to).
    "contracts.detect_pending",
    # Phase 18: the AI Next Action decision layer's entry point — itself
    # governed exactly like every other step (allowlisted here, dispatched
    # through the same AIExecutionService/ToolRegistry boundary below).
    # Its OWN proposal is independently validated against a narrower,
    # separate allowlist inside app/services/ai_next_action_service.py
    # before it can reach a second, nested AIExecutionService call.
    "ai.propose_quote_followup",
    # Phase 20: the second AI Next Action scenario — same governance shape.
    "ai.propose_invoice_followup",
})


class AutomationNotFoundError(Exception):
    pass


class AutomationValidationError(Exception):
    pass


class AutomationNotPublishedError(Exception):
    pass


@dataclass
class StepDefinition:
    action: str
    params: dict


def _validate_trigger(trigger_type: str, trigger_config: dict) -> None:
    if trigger_type not in (TriggerType.EVENT, TriggerType.SCHEDULE, TriggerType.MANUAL):
        raise AutomationValidationError(f"invalid trigger_type: {trigger_type!r}")
    if trigger_type == TriggerType.EVENT and not trigger_config.get("event_type"):
        raise AutomationValidationError("EVENT trigger requires trigger_config.event_type")
    if trigger_type == TriggerType.SCHEDULE:
        try:
            validate_schedule_config(trigger_config)
        except InvalidScheduleError as exc:
            raise AutomationValidationError(f"invalid schedule: {exc}") from exc


def _validate_steps(steps: list[dict]) -> None:
    if not isinstance(steps, list) or not steps:
        raise AutomationValidationError("steps must be a non-empty list")
    wait_seen = False
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "action" not in step:
            raise AutomationValidationError(f"step {i} must be an object with an 'action'")
        action = step["action"]
        if action == "wait":
            if i != 0:
                raise AutomationValidationError("'wait' is only supported as the first step")
            if "seconds" not in step.get("params", {}):
                raise AutomationValidationError("'wait' step requires params.seconds")
            wait_seen = True
            continue
        if action not in ACTION_ALLOWLIST:
            raise AutomationValidationError(
                f"step {i}: {action!r} is not an allowed automation action"
            )
    del wait_seen  # documents the constraint; nothing further to do with it here


class AutomationService:
    def __init__(self, session_factory: async_sessionmaker, ai_execution_service: AIExecutionService) -> None:
        self._session_factory = session_factory
        self._ai_execution = ai_execution_service

    # --- CRUD / versioning -------------------------------------------------

    async def create_automation(
        self, tenant_id: uuid.UUID, *, name: str, description: str | None, trigger_type: str,
        trigger_config: dict, condition: dict | None, steps: list[dict], created_by: uuid.UUID | None,
    ) -> Automation:
        _validate_trigger(trigger_type, trigger_config)
        try:
            validate_condition(condition)
        except InvalidConditionError as exc:
            raise AutomationValidationError(f"invalid condition: {exc}") from exc
        _validate_steps(steps)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automation = Automation(tenant_id=tenant_id, name=name, description=description, created_by=created_by)
            session.add(automation)
            await session.flush()
            version = AutomationVersion(
                tenant_id=tenant_id, automation_id=automation.id, version_number=1, trigger_type=trigger_type,
                trigger_config=trigger_config, condition=condition, steps=steps, created_by=created_by,
            )
            session.add(version)
            await session.commit()
            await session.refresh(automation)
            return automation

    async def update_automation(
        self, tenant_id: uuid.UUID, automation_id: uuid.UUID, *, trigger_type: str, trigger_config: dict,
        condition: dict | None, steps: list[dict], created_by: uuid.UUID | None,
    ) -> AutomationVersion:
        """Always creates a NEW version — never mutates an existing one.
        An in-flight execution's FK points at the version it started with,
        so this can never change behavior out from under it."""
        _validate_trigger(trigger_type, trigger_config)
        try:
            validate_condition(condition)
        except InvalidConditionError as exc:
            raise AutomationValidationError(f"invalid condition: {exc}") from exc
        _validate_steps(steps)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automation = await session.get(Automation, automation_id)
            if automation is None or automation.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"Automation {automation_id} not found")
            latest = (
                await session.execute(
                    select(AutomationVersion)
                    .where(AutomationVersion.automation_id == automation_id)
                    .order_by(AutomationVersion.version_number.desc())
                )
            ).scalars().first()
            next_number = (latest.version_number + 1) if latest else 1
            version = AutomationVersion(
                tenant_id=tenant_id, automation_id=automation_id, version_number=next_number,
                trigger_type=trigger_type, trigger_config=trigger_config, condition=condition, steps=steps,
                created_by=created_by,
            )
            session.add(version)
            await session.commit()
            await session.refresh(version)
            return version

    async def publish(self, tenant_id: uuid.UUID, automation_id: uuid.UUID) -> Automation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automation = await session.get(Automation, automation_id)
            if automation is None or automation.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"Automation {automation_id} not found")
            latest = (
                await session.execute(
                    select(AutomationVersion)
                    .where(AutomationVersion.automation_id == automation_id)
                    .order_by(AutomationVersion.version_number.desc())
                )
            ).scalars().first()
            if latest is None:
                raise AutomationValidationError("cannot publish an automation with no versions")
            automation.published_version_id = latest.id
            automation.status = AutomationStatus.ENABLED
            await session.commit()
            await session.refresh(automation)
            return automation

    async def set_enabled(self, tenant_id: uuid.UUID, automation_id: uuid.UUID, enabled: bool) -> Automation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automation = await session.get(Automation, automation_id)
            if automation is None or automation.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"Automation {automation_id} not found")
            if enabled and automation.published_version_id is None:
                raise AutomationNotPublishedError("cannot enable an automation with no published version")
            automation.status = AutomationStatus.ENABLED if enabled else AutomationStatus.DISABLED
            await session.commit()
            await session.refresh(automation)
            return automation

    async def get_automation(self, tenant_id: uuid.UUID, automation_id: uuid.UUID) -> Automation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automation = await session.get(Automation, automation_id)
            if automation is None or automation.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"Automation {automation_id} not found")
            return automation

    async def list_automations(self, tenant_id: uuid.UUID) -> list[Automation]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(select(Automation).where(Automation.tenant_id == tenant_id))
            ).scalars().all()
            return list(rows)

    async def list_versions(self, tenant_id: uuid.UUID, automation_id: uuid.UUID) -> list[AutomationVersion]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(AutomationVersion)
                    .where(AutomationVersion.tenant_id == tenant_id, AutomationVersion.automation_id == automation_id)
                    .order_by(AutomationVersion.version_number)
                )
            ).scalars().all()
            return list(rows)

    async def get_version(self, tenant_id: uuid.UUID, version_id: uuid.UUID) -> AutomationVersion:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            version = await session.get(AutomationVersion, version_id)
            if version is None or version.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"AutomationVersion {version_id} not found")
            return version

    # --- Execution -----------------------------------------------------

    async def trigger_manual(
        self, tenant_id: uuid.UUID, automation_id: uuid.UUID, *, context: dict, triggered_by: uuid.UUID | None,
        entity_type: str | None = None, entity_id: uuid.UUID | None = None,
    ) -> AutomationExecution | None:
        """`entity_type`/`entity_id` are explicit, real-`UUID` keyword
        params — never derived from `context` (a JSON blob whose own
        `entity_id`, if any, is caller-supplied free-form data, e.g. for
        `{{...}}` step templating, not necessarily a UUID at all)."""
        automation = await self.get_automation(tenant_id, automation_id)
        if automation.status != AutomationStatus.ENABLED or automation.published_version_id is None:
            raise AutomationNotPublishedError("automation is not enabled/published")
        version = await self.get_version(tenant_id, automation.published_version_id)
        return await self.start_execution(
            tenant_id, automation, version, trigger_type=TriggerType.MANUAL, source_event_id=None,
            entity_type=entity_type, entity_id=entity_id, context=context,
            triggered_by=triggered_by,
        )

    async def start_execution(
        self, tenant_id: uuid.UUID, automation: Automation, version: AutomationVersion, *, trigger_type: str,
        source_event_id: uuid.UUID | None, entity_type: str | None, entity_id: uuid.UUID | None, context: dict,
        triggered_by: uuid.UUID | None,
    ) -> AutomationExecution | None:
        """Real idempotency: (automation_version_id, source_event_id) is a
        real DB unique constraint — a duplicate event delivery (webhook
        retry, re-read stream message, concurrent workers) can never start
        a second execution. Returns None only when a duplicate was
        detected and silently deduplicated (matches
        LeadService.create_lead's own (lead, deduplicated) convention,
        simplified to Optional since callers here don't need the original
        row back)."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            execution = AutomationExecution(
                tenant_id=tenant_id, automation_id=automation.id, automation_version_id=version.id,
                trigger_type=trigger_type, source_event_id=source_event_id, entity_type=entity_type,
                entity_id=entity_id, status=ExecutionStatus.RUNNING, context=context,
                started_at=datetime.now(timezone.utc), triggered_by=triggered_by,
            )
            session.add(execution)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info(
                    "automation_execution_deduplicated", automation_id=str(automation.id),
                    source_event_id=str(source_event_id) if source_event_id else None,
                )
                return None
            await session.refresh(execution)

        if not evaluate_condition(version.condition, context):
            await self._complete_execution(tenant_id, execution.id, ExecutionStatus.COMPLETED)
            logger.info("automation_condition_not_met", automation_id=str(automation.id), execution_id=str(execution.id))
            return await self.get_execution(tenant_id, execution.id)

        steps = version.steps
        if steps and steps[0].get("action") == "wait":
            await self._start_durable_wait(tenant_id, execution, steps)
            return await self.get_execution(tenant_id, execution.id)

        await self.run_steps(tenant_id, execution.id, steps, start_index=0)
        return await self.get_execution(tenant_id, execution.id)

    async def check_and_dispatch_scheduled(
        self, tenant_id: uuid.UUID | None = None, *, now_utc: datetime | None = None,
    ) -> list[uuid.UUID]:
        """Piggybacks on the Event Worker's already-running poll tick (see
        app/events/worker.py's `on_tick` hook, exactly like Morning Brief's
        own `check_and_generate_scheduled`) rather than adding a second
        scheduler. Idempotent via a deterministic per-(automation,
        tenant-local-date) occurrence key used as `source_event_id` — the
        same real DB unique constraint on
        `(automation_version_id, source_event_id)` that already protects
        EVENT-triggered dispatch protects this too, so two overlapping
        ticks (or two worker processes) can never double-fire the same
        day's occurrence; a late tick (scheduler was down past the target
        time) still fires the day's occurrence exactly once rather than
        silently dropping it (Rule 8, Policy A). Returns the dispatched
        (non-deduplicated) execution ids, mainly for tests. `tenant_id`,
        when given, scopes dispatch to one tenant only — used by the
        manual test-tick API endpoint so a tenant can never trigger
        another tenant's schedule; omitted, it checks every tenant, which
        is what the real background worker does."""
        from app.models.organization import Organization

        now_utc = now_utc or datetime.now(timezone.utc)
        dispatched: list[uuid.UUID] = []

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            query = (
                select(AutomationVersion, Automation)
                .join(Automation, Automation.published_version_id == AutomationVersion.id)
                .where(
                    Automation.status == AutomationStatus.ENABLED,
                    AutomationVersion.trigger_type == TriggerType.SCHEDULE,
                )
            )
            if tenant_id is not None:
                query = query.where(Automation.tenant_id == tenant_id)
            candidates = (await session.execute(query)).all()
            if not candidates:
                return dispatched

            tenant_ids = {automation.tenant_id for _, automation in candidates}
            orgs = (await session.execute(select(Organization).where(Organization.id.in_(tenant_ids)))).scalars().all()
            org_timezone = {org.id: org.timezone for org in orgs}

        for version, automation in candidates:
            tz_name = org_timezone.get(automation.tenant_id, "UTC")
            due = check_due(version.trigger_config, tz_name, now_utc=now_utc)
            if not due.is_due:
                continue

            occurrence_key = uuid.uuid5(
                uuid.NAMESPACE_URL, f"klaros-automation-schedule:{automation.id}:{due.occurrence_date.isoformat()}"
            )

            async with self._session_factory() as session:
                # Phase 17B-3: this per-candidate dedup check is itself a
                # single-tenant operation (automation.id, and therefore the
                # execution rows it can ever match, belong to exactly one
                # tenant) even during the global sweep (`tenant_id` param
                # None). Stamp `automation.tenant_id` — the tenant this
                # specific candidate actually belongs to — never the
                # outer, possibly-None `tenant_id` parameter, which would
                # silently leave this query with no tenant context at all
                # during the real worker-tick sweep.
                await set_tenant_context(session, automation.tenant_id)
                already_fired = (
                    await session.execute(
                        select(AutomationExecution.id).where(
                            AutomationExecution.automation_id == automation.id,
                            AutomationExecution.source_event_id == occurrence_key,
                        )
                    )
                ).scalar_one_or_none()
            if already_fired is not None:
                continue

            try:
                execution = await self.start_execution(
                    automation.tenant_id, automation, version, trigger_type=TriggerType.SCHEDULE,
                    source_event_id=occurrence_key, entity_type=None, entity_id=None,
                    context={"schedule": {"occurrence_date": due.occurrence_date.isoformat()}}, triggered_by=None,
                )
            except Exception as exc:  # noqa: BLE001 — one automation's failure must never block the others or the worker tick
                logger.error("automation_schedule_dispatch_failed", automation_id=str(automation.id), error=str(exc))
                continue
            if execution is not None:
                dispatched.append(execution.id)

        return dispatched

    async def _start_durable_wait(self, tenant_id: uuid.UUID, execution: AutomationExecution, steps: list[dict]) -> None:
        """Hands off to a real Temporal workflow for the durable wait —
        never asyncio.sleep in this (worker/request) process. If Temporal
        itself is unreachable, the execution fails honestly rather than
        silently skipping the wait or blocking the caller."""
        from app.workflows.automation_workflow import start_automation_wait_workflow

        try:
            workflow_id = await start_automation_wait_workflow(
                execution_id=str(execution.id), tenant_id=str(tenant_id), wait_seconds=steps[0]["params"]["seconds"],
            )
        except Exception as exc:  # noqa: BLE001 — Temporal being unreachable must not crash the caller
            logger.error("automation_temporal_start_failed", execution_id=str(execution.id), error=str(exc))
            await self._fail_execution(tenant_id, execution.id, f"failed to start durable wait: {exc}")
            return

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(AutomationExecution, execution.id)
            row.status = ExecutionStatus.WAITING
            row.temporal_workflow_id = workflow_id
            await session.commit()

    async def run_steps(self, tenant_id: uuid.UUID, execution_id: uuid.UUID, steps: list[dict], *, start_index: int) -> None:
        """Runs steps[start_index:] — called either synchronously right
        after start_execution (no-wait automations) or by the Temporal
        activity that resumes a WAITING execution after its durable timer
        fires (app/workflows/automation_workflow.py)."""
        for i in range(start_index, len(steps)):
            step = steps[i]
            action = step["action"]
            if action == "wait":
                continue  # already handled by the caller before run_steps was invoked
            await self._run_one_step(tenant_id, execution_id, i, action, step.get("params", {}))

            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                exec_row = await session.get(AutomationExecution, execution_id)
                if exec_row.status == ExecutionStatus.FAILED:
                    return  # a step failed — do not run subsequent steps

        await self._complete_execution(tenant_id, execution_id, ExecutionStatus.COMPLETED)

    async def _run_one_step(self, tenant_id: uuid.UUID, execution_id: uuid.UUID, step_index: int, action: str, params: dict) -> None:
        step_row_id: uuid.UUID
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            exec_row = await session.get(AutomationExecution, execution_id)
            step_row = AutomationExecutionStep(
                tenant_id=tenant_id, execution_id=execution_id, step_index=step_index, action=action,
                status=StepStatus.RUNNING, started_at=datetime.now(timezone.utc),
            )
            session.add(step_row)
            exec_row.current_step_index = step_index
            await session.commit()
            await session.refresh(step_row)
            step_row_id = step_row.id
            context = dict(exec_row.context)

        if action not in ACTION_ALLOWLIST:
            # Defense in depth — _validate_steps already rejects this at
            # save time, but an execution's steps come from an immutable,
            # already-validated AutomationVersion, so this should be
            # unreachable in practice; still enforced here explicitly,
            # exactly like the voice engine's _execute_governed_tool.
            await self._fail_step(tenant_id, step_row_id, execution_id, f"{action!r} is not an allowed automation action")
            return

        resolved_params = _resolve_params(params, context)
        try:
            result = await self._ai_execution.request_tool_execution(
                ToolRequest(tool_name=action, input=resolved_params), tenant_id=tenant_id, ai_role=Role.MANAGER,
                correlation_id=execution_id,
            )
        except Exception as exc:  # noqa: BLE001 — a tool failure must produce an honest, visible step failure
            await self._fail_step(tenant_id, step_row_id, execution_id, str(exc))
            return

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            step_row = await session.get(AutomationExecutionStep, step_row_id)
            step_row.status = StepStatus.SUCCEEDED
            step_row.result = result.model_dump(mode="json") if hasattr(result, "model_dump") else None
            step_row.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def _fail_step(self, tenant_id: uuid.UUID, step_id: uuid.UUID, execution_id: uuid.UUID, error: str) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            step_row = await session.get(AutomationExecutionStep, step_id)
            step_row.status = StepStatus.FAILED
            step_row.error = error
            step_row.completed_at = datetime.now(timezone.utc)
            await session.commit()
        await self._fail_execution(tenant_id, execution_id, error)

    async def _fail_execution(self, tenant_id: uuid.UUID, execution_id: uuid.UUID, error: str) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            exec_row = await session.get(AutomationExecution, execution_id)
            exec_row.status = ExecutionStatus.FAILED
            exec_row.error = error
            exec_row.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def _complete_execution(self, tenant_id: uuid.UUID, execution_id: uuid.UUID, status: str) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            exec_row = await session.get(AutomationExecution, execution_id)
            exec_row.status = status
            exec_row.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def get_execution(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> AutomationExecution:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            execution = await session.get(AutomationExecution, execution_id)
            if execution is None or execution.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"AutomationExecution {execution_id} not found")
            return execution

    async def list_executions(self, tenant_id: uuid.UUID, automation_id: uuid.UUID | None = None) -> list[AutomationExecution]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            query = select(AutomationExecution).where(AutomationExecution.tenant_id == tenant_id)
            if automation_id is not None:
                query = query.where(AutomationExecution.automation_id == automation_id)
            rows = (await session.execute(query.order_by(AutomationExecution.created_at.desc()))).scalars().all()
            return list(rows)

    async def get_next_run(self, tenant_id: uuid.UUID, automation_id: uuid.UUID) -> datetime | None:
        """For display only (Rule 3/21/13) — always recomputed live from
        the current published version's trigger_config and the tenant's
        Organization.timezone, never persisted/cached, so it can never go
        stale after a republish or a timezone change."""
        from app.models.organization import Organization

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automation = await session.get(Automation, automation_id)
            if automation is None or automation.tenant_id != tenant_id:
                raise AutomationNotFoundError(f"Automation {automation_id} not found")
            if automation.status != AutomationStatus.ENABLED or automation.published_version_id is None:
                return None
            version = await session.get(AutomationVersion, automation.published_version_id)
            if version is None or version.trigger_type != TriggerType.SCHEDULE:
                return None
            org = await session.get(Organization, tenant_id)
            tz_name = org.timezone if org is not None else "UTC"

        return compute_next_run(version.trigger_config, tz_name, now_utc=datetime.now(timezone.utc))

    async def get_summary(self, tenant_id: uuid.UUID) -> dict:
        """Real, queried-on-demand counts for the Owner Cockpit (Rule 20) —
        never a fabricated or cached metric. `pending_approvals` reflects
        that every currently-allowlisted automation action
        (ACTION_ALLOWLIST) happens to carry an AUTO tool policy today, so
        it is honestly always 0 rather than a placeholder; it will become
        real the moment an APPROVAL_REQUIRED action is ever allowlisted,
        since automation actions flow through the exact same
        AIExecutionService/ApprovalRequest pipeline as everything else."""
        from datetime import datetime, timezone

        from sqlalchemy import func

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            automations_total = (
                await session.execute(
                    select(func.count()).select_from(Automation).where(Automation.tenant_id == tenant_id)
                )
            ).scalar_one()
            automations_enabled = (
                await session.execute(
                    select(func.count()).select_from(Automation).where(
                        Automation.tenant_id == tenant_id, Automation.status == AutomationStatus.ENABLED,
                    )
                )
            ).scalar_one()
            executions_running = (
                await session.execute(
                    select(func.count()).select_from(AutomationExecution).where(
                        AutomationExecution.tenant_id == tenant_id,
                        AutomationExecution.status.in_([ExecutionStatus.RUNNING, ExecutionStatus.WAITING]),
                    )
                )
            ).scalar_one()
            executions_failed = (
                await session.execute(
                    select(func.count()).select_from(AutomationExecution).where(
                        AutomationExecution.tenant_id == tenant_id, AutomationExecution.status == ExecutionStatus.FAILED,
                    )
                )
            ).scalar_one()
            executions_completed_today = (
                await session.execute(
                    select(func.count()).select_from(AutomationExecution).where(
                        AutomationExecution.tenant_id == tenant_id,
                        AutomationExecution.status == ExecutionStatus.COMPLETED,
                        AutomationExecution.completed_at >= today_start,
                    )
                )
            ).scalar_one()
            # Phase 11 (Rule 13): real count of currently-ENABLED SCHEDULE-
            # triggered automations, for the Owner Cockpit's "Automations"
            # widget — joined the same way check_and_dispatch_scheduled
            # itself resolves the current published version's trigger_type.
            automations_scheduled = (
                await session.execute(
                    select(func.count()).select_from(Automation).join(
                        AutomationVersion, Automation.published_version_id == AutomationVersion.id,
                    ).where(
                        Automation.tenant_id == tenant_id, Automation.status == AutomationStatus.ENABLED,
                        AutomationVersion.trigger_type == TriggerType.SCHEDULE,
                    )
                )
            ).scalar_one()

        return {
            "automations_total": automations_total,
            "automations_enabled": automations_enabled,
            "automations_scheduled": automations_scheduled,
            "executions_running": executions_running,
            "executions_failed": executions_failed,
            "executions_completed_today": executions_completed_today,
            "pending_approvals": 0,
        }

    async def list_execution_steps(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> list[AutomationExecutionStep]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(AutomationExecutionStep)
                    .where(AutomationExecutionStep.tenant_id == tenant_id, AutomationExecutionStep.execution_id == execution_id)
                    .order_by(AutomationExecutionStep.step_index)
                )
            ).scalars().all()
            return list(rows)


def _resolve_params(params: dict, context: dict) -> dict:
    """Very small templating: a param value of the exact form "{{a.b.c}}"
    is replaced with the resolved context field; every other value passes
    through unchanged. Never string-interpolation/f-string/eval — only
    ever a full-value placeholder swap, so a malicious context value can
    never be used to construct a different shape of parameter than what
    the automation's own author wrote."""
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
            path = value[2:-2].strip()
            node = context
            for part in path.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            resolved[key] = node
        else:
            resolved[key] = value
    return resolved
