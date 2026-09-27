"""Phase 4 (Agent Runtime foundation): the governed execution orchestrator.

This is the ONE place `agent.execute()`-shaped requests are handled — never
buried inline in the API route handler (KLAROS_FINAL_AGENT_MODEL.md's
governance chain, and this phase's own "keep this policy layer explicit and
separately testable" instruction). It performs exactly the sequence the
architecture requires, composing with — never duplicating or bypassing —
the existing, unchanged primitives:

  request -> load agent (tenant-scoped) -> active-agent check -> load
  active PUBLISHED version -> rate/concurrency limits -> build the governed
  ExecutionContext -> ToolRegistry.execute() (which itself now performs the
  two new agent-governance checks — see app/tools/registry.py — plus every
  pre-existing check: kill switch, RBAC, tenant scope, billing, schema,
  tool policy, approval) -> record AgentExecution -> audit.

No second execution engine: the only thing this service does that
ToolRegistry.execute() doesn't already do is (a) resolve which Agent +
AgentVersion a request means, (b) enforce the agent-scoped rate/concurrency
ceiling (KLAROS_FINAL_AGENT_MODEL.md governance chain step 9 — not a
ToolRegistry-level concept, since it's specific to one agent's own
execution history), and (c) persist the `AgentExecution` audit/durability
record. Every authorization decision itself still happens inside
ToolRegistry.execute() and the existing AIExecutionService pattern it
already established (app/ai/execution_service.py) is mirrored here, not
replaced: this service hands ToolRegistry a fully-formed request and does
not itself decide whether the call is allowed.

No autonomous loop: `run_action` executes exactly ONE declared tool call
per invocation (`max_tool_chain_depth` on AgentVersion is modeled for a
later phase's multi-step chains; enforced here only as "chain depth 1 is
always satisfied by a single call", never as a loop).

Phase 7 (Agent Runtime Reliability II) additions — see
PHASE_7_IMPLEMENTATION_LOG.md for the full design. Summary:

  - A SINGLE_ACTION execution now gets the exact same durable step
    boundary a REASONING execution's first step gets: `run_action` writes
    an `AgentExecutionStep` row (`step_number=1`, `TOOL_CALL`,
    `completed_at=NULL`) BEFORE calling `ToolRegistry.execute()`, never
    after — the same "row exists before the external call" invariant
    `AgentReasoningService._attempt_tool_call` already established. This
    closes the exact gap Phase 6 documented and deliberately left open
    (`AgentRecoveryService`'s old "never resume a SINGLE_ACTION execution"
    branch — see that module's Phase 7 update).
  - `resume_recovered` (called only by `AgentRecoveryService`, after it has
    already atomically claimed the execution's lease) is the SINGLE_ACTION
    mirror of `AgentReasoningService.resume_recovered` — it inspects the
    durable step and decides, deterministically, whether the tool already
    ran, never guessing (see its own docstring for the exact decision
    table).
  - A deterministic, server-generated idempotency identity
    (`_idempotency_identity` — `f"agent-exec:{execution_id}:{step_number}"`)
    is threaded through `ExecutionContext.idempotency_key` on every
    governed call this service makes. It is fully reconstructible from
    durable state alone (the step's own `execution_id`/`step_number`, both
    already persisted) — no additional column was needed to "store" it.
  - Bug fix: before this phase, `_mark_running` never set
    `execution_owner_id`/`lease_expires_at`/`heartbeat_at` for a
    SINGLE_ACTION execution — meaning a SINGLE_ACTION execution was, for
    its entire (normally sub-second) RUNNING window, already a "stale"
    candidate by `AgentRecoveryService._find_stale_candidates`'s own
    `lease_expires_at IS NULL` test. In the old code this was almost never
    observed (recovery immediately halted the execution regardless, and a
    sweep racing a live SINGLE_ACTION call is rare), but it was a real gap
    that would have let a recovery sweep claim and prematurely act on a
    SINGLE_ACTION execution that had not actually crashed. Fixed by having
    `_mark_running` claim the same lease `AgentReasoningService.start` and
    `resume_after_approval` already claim for themselves."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.actor import ActorType
from app.models.agent import (
    Agent,
    AgentExecution,
    AgentExecutionMode,
    AgentExecutionStatus,
    AgentExecutionStep,
    AgentExecutionStepStatus,
    AgentExecutionStepType,
    AgentExecutionTerminationReason,
    AgentStatus,
    AgentTriggerSource,
    AgentVersion,
    AgentVersionStatus,
)
from app.models.audit_log import AuditLog
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError, ToolError
from app.tools.redact import redact_input
from app.tools.registry import ToolRegistry

# Phase 6 (crash recovery), now centralized here — the ONE lease-duration
# constant for every AgentExecution, REASONING or SINGLE_ACTION.
# AgentReasoningService imports this same constant (it already imports
# other names from this module; this avoids a circular import the other
# way around, since agent_reasoning_service.py imports
# AgentExecutionService itself).
EXECUTION_LEASE_DURATION = timedelta(minutes=5)

# SINGLE_ACTION is, by construction, a bounded-to-one-step execution — this
# is the one and only step_number it will ever write (see module
# docstring).
_SINGLE_ACTION_STEP_NUMBER = 1


class AgentExecutionError(Exception):
    pass


class AgentNotExecutableError(AgentExecutionError):
    """Agent is not ACTIVE, has no PUBLISHED current version, is
    tenant-mismatched, or has hit its own rate/concurrency ceiling."""


class DuplicateExecutionRequestError(AgentExecutionError):
    """An execution with this idempotency key already exists for this
    tenant+agent — the caller should fetch and return that one instead of
    creating a second side effect (Execution Safety requirement)."""

    def __init__(self, existing_execution_id: uuid.UUID) -> None:
        super().__init__("An execution with this idempotency key already exists")
        self.existing_execution_id = existing_execution_id


class AgentExecutionService:
    def __init__(
        self, session_factory: async_sessionmaker, tool_registry: ToolRegistry, *, worker_id: uuid.UUID | None = None
    ) -> None:
        self._session_factory = session_factory
        self._registry = tool_registry
        # Phase 7: this process/instance's own execution-lease owner id —
        # identical pattern to AgentReasoningService's own `_worker_id`
        # (see that module's docstring). A fresh, random id per instance
        # unless a caller supplies one explicitly (AgentRecoveryService
        # supplies its own distinct worker_id when it resumes a
        # SINGLE_ACTION execution, since a recovery worker is conceptually
        # a different "process" than whatever originally started it).
        self._worker_id = worker_id or uuid.uuid4()

    @staticmethod
    def _idempotency_identity(execution_id: uuid.UUID, step_number: int) -> str:
        """Phase 7: the deterministic tool-call identity for one logical
        governed operation — see module docstring. ALWAYS the same string
        for the same (execution_id, step_number) pair, across retries,
        crash recovery, and process restarts; never derived from anything
        random, from wall-clock time, or from any LLM/model output."""
        return f"agent-exec:{execution_id}:{step_number}"

    async def run_action(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        tool_name: str,
        tool_input: dict,
        triggered_by: uuid.UUID | None,
        trigger_source: str = AgentTriggerSource.MANUAL,
        idempotency_key: str | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> AgentExecution:
        agent, version = await self._load_executable(tenant_id, agent_id)

        if idempotency_key:
            existing = await self._find_by_idempotency_key(tenant_id, agent_id, idempotency_key)
            if existing is not None:
                raise DuplicateExecutionRequestError(existing.id)

        await self._enforce_rate_and_concurrency(tenant_id, agent, version)

        execution = await self._create_execution_row(
            tenant_id, agent, version, tool_name=tool_name, tool_input=tool_input,
            triggered_by=triggered_by, trigger_source=trigger_source,
            idempotency_key=idempotency_key, correlation_id=correlation_id,
        )

        # Phase 7: the durable step boundary must exist BEFORE the external
        # tool call is ever attempted — never after (see module docstring
        # "Critical crash boundaries"). This single write is what makes
        # `resume_recovered` able to distinguish "nothing happened yet"
        # from "the tool call was in flight" after a crash.
        await self._mark_running(execution.id)
        await self._create_step(tenant_id, execution.id, tool_name=tool_name, tool_input=tool_input)

        return await self._execute_tool_and_finish(
            tenant_id, execution.id,
            agent_id=agent.id, version_id=version.id, acting_role=agent.acting_role,
            tool_name=tool_name, tool_input=tool_input,
        )

    # --------------------------------------------------- Phase 7: execution

    async def _execute_tool_and_finish(
        self, tenant_id: uuid.UUID, execution_id: uuid.UUID, *,
        agent_id: uuid.UUID, version_id: uuid.UUID, acting_role: str,
        tool_name: str, tool_input: dict,
    ) -> AgentExecution:
        """The one place this service actually calls `ToolRegistry.execute()`
        — used by both a fresh `run_action` call and a `resume_recovered`
        retry (Case 1 of the Unknown External Outcome policy: a tool with
        verified idempotency support). Always carries the SAME deterministic
        `idempotency_key` for a given (execution_id, step_number), so a
        retry through this method is, for a supporting tool, safe by
        construction."""
        context = ExecutionContext(
            tenant_id=tenant_id,
            actor_type=ActorType.AGENT,
            actor_id=agent_id,
            role=Role(acting_role),
            # The AgentExecution's own id doubles as the correlation id
            # that links a resulting ApprovalRequest back to this
            # execution — see app/tools/registry.py::_create_approval_
            # request and app/services/approval_execution_service.py's
            # Phase 4 additions, both of which read
            # ExecutionContext.correlation_id for exactly this purpose.
            correlation_id=execution_id,
            agent_id=agent_id,
            agent_version_id=version_id,
            idempotency_key=self._idempotency_identity(execution_id, _SINGLE_ACTION_STEP_NUMBER),
        )

        try:
            output = await self._registry.execute(tool_name, tool_input, context)
        except ToolApprovalRequiredError as exc:
            await self._update_step(
                tenant_id, execution_id, status=AgentExecutionStepStatus.APPROVAL_REQUIRED
            )
            return await self._mark_waiting_approval(execution_id, approval_request_id=exc.approval_request_id)
        except ToolError as exc:
            await self._update_step(
                tenant_id, execution_id, status=AgentExecutionStepStatus.FAILED,
                error_code=str(exc)[:100],
            )
            return await self._mark_failed(execution_id, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — always recorded, never swallowed
            await self._update_step(
                tenant_id, execution_id, status=AgentExecutionStepStatus.FAILED, error_code="execution_error",
            )
            return await self._mark_failed(execution_id, error=str(exc))

        result = output.model_dump(mode="json") if hasattr(output, "model_dump") else None
        await self._update_step(
            tenant_id, execution_id, status=AgentExecutionStepStatus.EXECUTED,
            output_summary=redact_input(result) if isinstance(result, dict) else None,
        )
        return await self._mark_completed(execution_id, result=result)

    # ----------------------------------------------- Phase 7: crash recovery

    async def resume_recovered(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> AgentExecution:
        """Called ONLY by `AgentRecoveryService`, after it has already
        atomically claimed this SINGLE_ACTION execution's lease. Implements
        the recovery decision table (see PHASE_7_IMPLEMENTATION_LOG.md
        §8/§9):

          - no step row exists yet (crash boundary A/B — nothing was ever
            durably recorded about a tool call actually starting): create
            the step now and attempt the tool exactly once, through the
            exact same governed path a fresh call would use.
          - the step row is already terminal (`completed_at IS NOT NULL` —
            boundary F, or the step reached APPROVAL_REQUIRED before the
            crash): the tool is NEVER called again; the execution is
            finished purely from the step's own durable outcome.
          - the step row is mid-flight (`completed_at IS NULL` — boundaries
            C/D/E, the genuinely ambiguous case): the tool's own verified
            `supports_idempotency` flag decides. True -> retry through
            `ToolRegistry.execute()` with the SAME deterministic
            idempotency identity the original attempt used (Case 1). False
            -> never guess; safe-halt for a human to inspect (Case 2)."""
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            if execution is None or execution.tenant_id != tenant_id:
                raise AgentExecutionError("Execution not found")
            agent = await session.get(Agent, execution.agent_id)
            version = await session.get(AgentVersion, execution.agent_version_id)
            step = (
                await session.execute(
                    select(AgentExecutionStep).where(
                        AgentExecutionStep.execution_id == execution_id,
                        AgentExecutionStep.step_number == _SINGLE_ACTION_STEP_NUMBER,
                    )
                )
            ).scalar_one_or_none()
            tool_name = execution.tool_name
            tool_input = dict(execution.tool_input_summary or {})
            acting_role = agent.acting_role
            agent_id = agent.id
            version_id = version.id
            step_status = step.status if step is not None else None
            step_completed = step.completed_at is not None if step is not None else False
            step_output_summary = step.output_summary if step is not None else None
            step_error_code = step.error_code if step is not None else None

        if step is None:
            # Boundary A/B: no durable signal a tool call was ever
            # attempted — safe to create the step now and run it, exactly
            # once, using the deterministic identity a first attempt would
            # have used.
            await self._create_step(tenant_id, execution_id, tool_name=tool_name, tool_input=tool_input)
            return await self._execute_tool_and_finish(
                tenant_id, execution_id, agent_id=agent_id, version_id=version_id,
                acting_role=acting_role, tool_name=tool_name, tool_input=tool_input,
            )

        if step_completed:
            # Boundary F: a terminal outcome for this step already exists —
            # the tool is NEVER called again; only the AgentExecution row's
            # own terminal status (which may not have been persisted before
            # the crash) needs to be finished from that durable record.
            if step_status == AgentExecutionStepStatus.EXECUTED:
                return await self._mark_completed(execution_id, result=step_output_summary)
            if step_status == AgentExecutionStepStatus.APPROVAL_REQUIRED:
                # Already durably parked on ApprovalExecutionService's own
                # governed resume path — nothing for this service to do.
                async with self._session_factory() as session:
                    return await session.get(AgentExecution, execution_id)
            return await self._mark_failed(
                execution_id,
                error=f"Tool call ended in {step_status} before the crash (error_code={step_error_code})",
            )

        # Boundaries C/D/E: the step was left mid-flight by the crash — the
        # true external outcome is unknown to this process. Never guess.
        tool = self._registry.get(tool_name) if tool_name else None
        if tool is not None and tool.supports_idempotency:
            # Case 1 (Unknown External Outcome policy): verified idempotency
            # support — reuse the SAME deterministic identity and retry.
            await self._audit(execution_id, "agent.execution.tool.idempotency_reused")
            return await self._execute_tool_and_finish(
                tenant_id, execution_id, agent_id=agent_id, version_id=version_id,
                acting_role=acting_role, tool_name=tool_name, tool_input=tool_input,
            )

        # Case 2: no verified idempotency support — safe-halt instead of
        # guessing, leaving a clear, auditable, human-actionable record.
        await self._update_step(
            tenant_id, execution_id, status=AgentExecutionStepStatus.FAILED, error_code="interrupted_by_crash",
        )
        await self._audit(execution_id, "agent.execution.tool.idempotency_ambiguous")
        return await self._halt_ambiguous(execution_id)

    async def _halt_ambiguous(self, execution_id: uuid.UUID) -> AgentExecution:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            execution.status = AgentExecutionStatus.HALTED
            execution.termination_reason = AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT
            execution.error_message = (
                "This tool call's external outcome is unknown after a process crash, and this tool has no "
                "verified idempotency support — it was never automatically retried. Inspect manually and, "
                "if appropriate, re-issue the request."
            )[:2000]
            execution.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(execution)
            return execution

    async def _audit(self, execution_id: uuid.UUID, action: str) -> None:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            if execution is None:
                return
            session.add(
                AuditLog(
                    tenant_id=execution.tenant_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    action=action,
                    tool=execution.tool_name,
                    entity_type="agent_execution",
                    entity_id=execution.id,
                    result="success",
                    correlation_id=execution.id,
                )
            )
            await session.commit()

    # ------------------------------------------------------- Phase 7: steps

    async def _create_step(
        self, tenant_id: uuid.UUID, execution_id: uuid.UUID, *, tool_name: str, tool_input: dict
    ) -> None:
        """Writes the SINGLE_ACTION step's durable "in flight" marker —
        `completed_at=NULL` is the same signature `AgentReasoningService.
        _reconcile_interrupted_step` already treats as "still mid-flight"
        for REASONING steps; this service's own `resume_recovered` keys off
        the identical signal."""
        async with self._session_factory() as session:
            session.add(
                AgentExecutionStep(
                    tenant_id=tenant_id, execution_id=execution_id, step_number=_SINGLE_ACTION_STEP_NUMBER,
                    step_type=AgentExecutionStepType.TOOL_CALL, status=AgentExecutionStepStatus.EXECUTED,
                    tool_name=tool_name,
                    input_summary=redact_input(tool_input) if isinstance(tool_input, dict) else None,
                    started_at=datetime.now(timezone.utc), completed_at=None,
                )
            )
            await session.commit()

    async def _update_step(
        self, tenant_id: uuid.UUID, execution_id: uuid.UUID, *,
        status: str, output_summary: dict | None = None, error_code: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentExecutionStep).where(
                        AgentExecutionStep.execution_id == execution_id,
                        AgentExecutionStep.step_number == _SINGLE_ACTION_STEP_NUMBER,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            if output_summary is not None:
                row.output_summary = output_summary
            if error_code is not None:
                row.error_code = error_code[:100]
            row.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def list_steps(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> list[AgentExecutionStep]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AgentExecutionStep)
                    .where(AgentExecutionStep.execution_id == execution_id, AgentExecutionStep.tenant_id == tenant_id)
                    .order_by(AgentExecutionStep.step_number)
                )
            ).scalars().all()
            return list(rows)

    # ------------------------------------------------------------- Loading

    async def _load_executable(self, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> tuple[Agent, AgentVersion]:
        async with self._session_factory() as session:
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AgentNotExecutableError("Agent not found")
            if agent.status != AgentStatus.ACTIVE:
                raise AgentNotExecutableError(f"Agent is not ACTIVE (status={agent.status})")
            if agent.current_version_id is None:
                raise AgentNotExecutableError("Agent has no published version")

            version = await session.get(AgentVersion, agent.current_version_id)
            if version is None or version.status != AgentVersionStatus.PUBLISHED:
                raise AgentNotExecutableError("Agent's current version is not PUBLISHED")

            return _snapshot(agent), _snapshot(version)

    async def _find_by_idempotency_key(
        self, tenant_id: uuid.UUID, agent_id: uuid.UUID, key: str
    ) -> AgentExecution | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentExecution).where(
                        AgentExecution.tenant_id == tenant_id,
                        AgentExecution.agent_id == agent_id,
                        AgentExecution.idempotency_key == key,
                    )
                )
            ).scalar_one_or_none()
            return _snapshot(row) if row is not None else None

    async def _enforce_rate_and_concurrency(
        self, tenant_id: uuid.UUID, agent: Agent, version: AgentVersion
    ) -> None:
        """KLAROS_FINAL_AGENT_MODEL.md governance chain step 9."""
        async with self._session_factory() as session:
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            hourly_count = (
                await session.execute(
                    select(func.count(AgentExecution.id)).where(
                        AgentExecution.tenant_id == tenant_id,
                        AgentExecution.agent_id == agent.id,
                        AgentExecution.created_at >= one_hour_ago,
                    )
                )
            ).scalar_one()
            if hourly_count >= version.max_executions_per_hour:
                raise AgentNotExecutableError(
                    f"Agent has reached its hourly execution ceiling ({version.max_executions_per_hour})"
                )

            in_flight = (
                await session.execute(
                    select(func.count(AgentExecution.id)).where(
                        AgentExecution.tenant_id == tenant_id,
                        AgentExecution.agent_id == agent.id,
                        AgentExecution.status.in_(
                            [AgentExecutionStatus.PENDING, AgentExecutionStatus.RUNNING,
                             AgentExecutionStatus.WAITING_APPROVAL]
                        ),
                    )
                )
            ).scalar_one()
            if in_flight >= version.max_concurrent_executions:
                raise AgentNotExecutableError(
                    f"Agent has reached its concurrent execution ceiling ({version.max_concurrent_executions})"
                )

    # -------------------------------------------------------------- Record

    async def _create_execution_row(
        self, tenant_id, agent: Agent, version: AgentVersion, *, tool_name, tool_input,
        triggered_by, trigger_source, idempotency_key, correlation_id,
    ) -> AgentExecution:
        """Phase 6 bug fix (found via a genuine concurrent-request repro
        against real Postgres while writing this phase's trigger-dispatch
        concurrency tests — see PHASE_6_IMPLEMENTATION_LOG.md §13): the
        pre-existing Phase 4 `_find_by_idempotency_key` check in
        `run_action` above is a check-THEN-insert pattern with a real race
        window — two genuinely concurrent callers with the SAME
        idempotency key can both pass the pre-check and both reach this
        insert. Before this fix, the second insert's real DB unique-
        constraint violation (`uq_agent_executions_tenant_agent_
        idempotency`) propagated as a raw, unhandled `IntegrityError`
        instead of the intended `DuplicateExecutionRequestError` — exactly
        the "duplicate scheduler delivery / duplicate event delivery must
        resolve to one logical execution" guarantee Phase 6's trigger
        dispatch depends on. Fixed the same way `AutomationService.
        start_execution` already handles the identical race (see
        automation_service.py): catch the IntegrityError on commit, roll
        back, and re-raise as `DuplicateExecutionRequestError` pointing at
        whichever row actually won."""
        async with self._session_factory() as session:
            execution = AgentExecution(
                tenant_id=tenant_id, agent_id=agent.id, agent_version_id=version.id,
                trigger_source=trigger_source, triggered_by=triggered_by,
                status=AgentExecutionStatus.PENDING, tool_name=tool_name,
                tool_input_summary=redact_input(tool_input), idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
            session.add(execution)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if idempotency_key:
                    existing = await self._find_by_idempotency_key(tenant_id, agent.id, idempotency_key)
                    if existing is not None:
                        raise DuplicateExecutionRequestError(existing.id) from None
                raise
            await session.refresh(execution)
            return _snapshot(execution)

    async def _mark_running(self, execution_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            now = datetime.now(timezone.utc)
            execution.status = AgentExecutionStatus.RUNNING
            execution.started_at = now
            # Phase 7 bug fix (see module docstring): claim the same
            # execution lease AgentReasoningService already claims for
            # itself, so a recovery sweep's staleness test never mistakes a
            # genuinely still-running SINGLE_ACTION execution for a crashed
            # one.
            execution.execution_owner_id = self._worker_id
            execution.lease_expires_at = now + EXECUTION_LEASE_DURATION
            execution.heartbeat_at = now
            await session.commit()

    async def _mark_completed(self, execution_id: uuid.UUID, *, result: dict | None) -> AgentExecution:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            execution.status = AgentExecutionStatus.COMPLETED
            execution.result_summary = result
            execution.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(execution)
            return _snapshot(execution)

    async def _mark_failed(self, execution_id: uuid.UUID, *, error: str) -> AgentExecution:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            execution.status = AgentExecutionStatus.FAILED
            execution.error_message = error[:2000]
            execution.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(execution)
            return _snapshot(execution)

    async def _mark_waiting_approval(
        self, execution_id: uuid.UUID, *, approval_request_id: uuid.UUID
    ) -> AgentExecution:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            execution.status = AgentExecutionStatus.WAITING_APPROVAL
            execution.approval_request_id = approval_request_id
            await session.commit()
            await session.refresh(execution)
            return _snapshot(execution)

    async def get_execution(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> AgentExecution:
        async with self._session_factory() as session:
            execution = await session.get(AgentExecution, execution_id)
            if execution is None or execution.tenant_id != tenant_id:
                raise AgentExecutionError("Execution not found")
            return _snapshot(execution)


def _snapshot(obj):
    return obj
