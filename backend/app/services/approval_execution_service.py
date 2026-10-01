"""Phase 9: closes the "approval dead end" named in every phase since 5 —
approving an `ApprovalRequest` now actually resumes and executes the
original tool call, through the same `ToolRegistry` every other action goes
through (never a direct domain-service call).

    ApprovalExecutionService
            |
       ToolRegistry.execute(..., skip_approval_gate=True)
            |
          Policy (BLOCKED still checked; APPROVAL_REQUIRED skipped — already approved)
            |
           Tool
            |
       Domain service

Two independent state machines, on purpose:
  - `ApprovalStatus`: PENDING -> APPROVED | REJECTED (a human decision)
  - `ApprovalExecutionStatus`: NOT_STARTED -> EXECUTING -> EXECUTED | FAILED
    (whether the original action has actually run)

Both transitions use a database-level compare-and-swap (`UPDATE ... WHERE
<current-state>`, checked via rowcount) rather than a Python lock, so two
concurrent requests — a double-click, a browser retry, two workers racing —
can never both "win" the same transition. This is the same pattern
`EventBus._handle_one`'s unique-constraint race-recovery uses (Phase 8),
applied here as a conditional UPDATE instead of a unique-insert, since the
resource here is a single existing row rather than a new one.

Phase 19 (Learn): the APPROVED/REJECTED transition is also the real
boundary where a human decision about an AI-proposed action becomes
durable — see `_learn_from_ai_decision` below. This is NOT autonomous
self-learning: the AI never decides what to remember, and never writes an
ACTIVE memory. A human's approve/reject click is the only trigger, and the
result always enters through the existing, unchanged
`CompanyMemoryService.propose_memory()` (always PENDING, source=
AI_PROPOSED) — a second, separate human action (the existing memory
confirm/reject flow, unchanged since Phase 13) decides whether it ever
becomes ACTIVE context for a future AI decision. Idempotent for free: the
`ApprovalStatus` CAS above already guarantees this code path is reached at
most once per `ApprovalRequest` — no second/duplicate memory-dedup
mechanism is added.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.actor import ActorType
from app.models.approval import ApprovalExecutionStatus, ApprovalRequest, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.company_memory import MAX_VALUE_LENGTH, MemoryType
from app.models.event import EventType
from app.models.rbac import Role
from app.models.user import User
from app.services.company_memory_service import CompanyMemoryService, MemoryValidationError
from app.tools.base import ExecutionContext
from app.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


class ApprovalNotFoundError(Exception):
    pass


class ApprovalStateError(Exception):
    """An invalid state transition was attempted (already decided, wrong
    execution state for the requested operation, etc). Mapped to HTTP 409."""


class SelfApprovalError(Exception):
    """The user who requested the action tried to approve their own request."""


class ApprovalExecutionService:
    def __init__(
        self, session_factory: async_sessionmaker, registry: ToolRegistry, bus: EventBus, *, ai_provider=None
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._bus = bus
        # Phase 19: internally constructed, no new required constructor
        # arg — same pattern as every other Company Memory consumer
        # (Morning Brief, Qualification, Marketing, SEO, Knowledge Q&A,
        # AI Next Action).
        self._memory = CompanyMemoryService(session_factory)
        # Phase 5: optional override for the AgentReasoningService this
        # service lazily constructs to continue a REASONING-mode
        # execution after an approval resumes (see
        # `_record_execution_outcome` below) — None (the default) means
        # "use the real, process-wide AIProvider" in production; tests
        # inject a scripted provider so the full approve -> resume ->
        # continue -> complete/halt path is exercised end to end, not
        # just the single-tool-call resume Phase 4 already covered.
        self._reasoning_ai_provider = ai_provider

    async def _load(self, session, tenant_id: uuid.UUID, approval_id: uuid.UUID) -> ApprovalRequest:
        request = await session.get(ApprovalRequest, approval_id)
        # Tenant isolation: a request belonging to another tenant is treated
        # as not found, never leaked via a different error shape — the same
        # pattern used for every other tenant-scoped lookup in this codebase.
        if request is None or request.tenant_id != tenant_id:
            raise ApprovalNotFoundError("Approval request not found")
        return request

    async def approve(
        self,
        tenant_id: uuid.UUID,
        approval_id: uuid.UUID,
        *,
        decided_by_id: uuid.UUID,
        decided_by_role: Role,
        note: str | None = None,
    ) -> ApprovalRequest:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            request = await self._load(session, tenant_id, approval_id)

            if request.requested_by_type == ActorType.USER and request.requested_by_id == decided_by_id:
                raise SelfApprovalError("The requester cannot approve their own request")

            now = datetime.now(timezone.utc)
            cas = await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == ApprovalStatus.PENDING)
                .values(status=ApprovalStatus.APPROVED, decided_by=decided_by_id, decision_note=note, decided_at=now)
            )
            await session.commit()
            if cas.rowcount != 1:
                current = await self._load(session, tenant_id, approval_id)
                raise ApprovalStateError(f"Approval already {current.status} — cannot approve again")
            correlation_id = request.correlation_id

        await self._publish(tenant_id, EventType.APPROVAL_APPROVED, approval_id, {"decided_by": str(decided_by_id)}, correlation_id=correlation_id)
        await self._audit(
            tenant_id, decided_by_id, "approval.approve", approval_id=approval_id,
            correlation_id=correlation_id, result="success",
        )
        await self._learn_from_ai_decision(tenant_id, request, decision="APPROVED", decision_note=note)

        return await self.execute_approved(tenant_id, approval_id)

    async def reject(
        self,
        tenant_id: uuid.UUID,
        approval_id: uuid.UUID,
        *,
        decided_by_id: uuid.UUID,
        note: str | None = None,
    ) -> ApprovalRequest:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            request = await self._load(session, tenant_id, approval_id)

            now = datetime.now(timezone.utc)
            cas = await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == ApprovalStatus.PENDING)
                .values(status=ApprovalStatus.REJECTED, decided_by=decided_by_id, decision_note=note, decided_at=now)
            )
            await session.commit()
            if cas.rowcount != 1:
                current = await self._load(session, tenant_id, approval_id)
                raise ApprovalStateError(f"Approval already {current.status} — cannot reject again")

            if request.agent_execution_id is not None:
                # Phase 4: a rejected approval means the originating
                # AgentExecution never runs — HALTED, not FAILED (FAILED is
                # reserved for the tool actually having been attempted and
                # erroring; a rejection means it was never attempted at
                # all), matching KLAROS_FINAL_AGENT_MODEL.md governance
                # chain step 12's "or surfaces to the approval/notification
                # queue" language — the halt IS the surfaced outcome here.
                from app.models.agent import AgentExecution, AgentExecutionStatus

                execution = await session.get(AgentExecution, request.agent_execution_id)
                if execution is not None and execution.tenant_id == tenant_id:
                    execution.status = AgentExecutionStatus.HALTED
                    execution.error_message = "Approval request was rejected"
                    execution.completed_at = now
                    await session.commit()

            refreshed = await self._load(session, tenant_id, approval_id)
            result = _detached_copy(refreshed)
            correlation_id = request.correlation_id

        await self._publish(tenant_id, EventType.APPROVAL_REJECTED, approval_id, {"decided_by": str(decided_by_id)}, correlation_id=correlation_id)
        await self._audit(
            tenant_id, decided_by_id, "approval.reject", approval_id=approval_id,
            correlation_id=correlation_id, result="success",
        )
        await self._learn_from_ai_decision(tenant_id, request, decision="REJECTED", decision_note=note)
        return result

    async def execute_approved(self, tenant_id: uuid.UUID, approval_id: uuid.UUID) -> ApprovalRequest:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            request = await self._load(session, tenant_id, approval_id)
            if request.status != ApprovalStatus.APPROVED:
                raise ApprovalStateError("Approval must be APPROVED before its action can execute")

            # The actual concurrency guarantee: only one caller can move
            # NOT_STARTED -> EXECUTING for this row. A second concurrent
            # caller (double-click, retry, another worker) gets rowcount=0
            # and returns the current state without touching the tool.
            cas = await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id, ApprovalRequest.execution_status == ApprovalExecutionStatus.NOT_STARTED)
                .values(
                    execution_status=ApprovalExecutionStatus.EXECUTING,
                    execution_attempts=ApprovalRequest.execution_attempts + 1,
                )
            )
            await session.commit()
            claimed = cas.rowcount == 1
            current = await self._load(session, tenant_id, approval_id)
            if not claimed:
                logger.info(
                    "approval_execution_already_claimed_or_done",
                    approval_id=str(approval_id),
                    execution_status=current.execution_status,
                )
                return _detached_copy(current)
            snapshot = _detached_copy(current)

        await self._publish(
            tenant_id, EventType.APPROVAL_EXECUTION_STARTED, approval_id,
            {"tool_name": snapshot.tool_name}, correlation_id=snapshot.correlation_id,
        )

        context = await self._reconstruct_context(tenant_id, snapshot)

        try:
            output = await self._registry.execute(
                snapshot.tool_name, snapshot.tool_input, context, skip_approval_gate=True
            )
            execution_result = output.model_dump(mode="json") if hasattr(output, "model_dump") else None
        except Exception as exc:  # noqa: BLE001 — must always record, never leave EXECUTING forever
            return await self._record_execution_outcome(
                tenant_id, approval_id, success=False, error=str(exc), correlation_id=snapshot.correlation_id
            )

        return await self._record_execution_outcome(
            tenant_id, approval_id, success=True, result=execution_result, correlation_id=snapshot.correlation_id
        )

    async def retry_failed(
        self, tenant_id: uuid.UUID, approval_id: uuid.UUID, *, actor_id: uuid.UUID
    ) -> ApprovalRequest:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            request = await self._load(session, tenant_id, approval_id)
            if request.status != ApprovalStatus.APPROVED or request.execution_status != ApprovalExecutionStatus.FAILED:
                raise ApprovalStateError("Only a FAILED execution can be retried")

            cas = await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id, ApprovalRequest.execution_status == ApprovalExecutionStatus.FAILED)
                .values(execution_status=ApprovalExecutionStatus.NOT_STARTED, execution_error=None)
            )
            await session.commit()
            if cas.rowcount != 1:
                # Someone else's retry (or a fresh execution) already re-armed it — idempotent no-op.
                current = await self._load(session, tenant_id, approval_id)
                return _detached_copy(current)

        await self._audit(
            tenant_id, actor_id, "approval.execution.retry", approval_id=approval_id,
            correlation_id=request.correlation_id, result="success",
        )
        return await self.execute_approved(tenant_id, approval_id)

    async def _record_execution_outcome(
        self,
        tenant_id: uuid.UUID,
        approval_id: uuid.UUID,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        correlation_id: uuid.UUID | None,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        continue_reasoning_execution_id: uuid.UUID | None = None
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            request = await self._load(session, tenant_id, approval_id)
            request.execution_status = ApprovalExecutionStatus.EXECUTED if success else ApprovalExecutionStatus.FAILED
            request.execution_result = result
            request.execution_error = error
            request.executed_at = now
            if request.agent_execution_id is not None:
                # Phase 4/5: closes the approval-integration loop for an
                # Agent-initiated call — the AgentExecution that was parked
                # in WAITING_APPROVAL now reflects the resumed outcome.
                # Best-effort: a missing/foreign row never blocks recording
                # the ApprovalRequest's own (authoritative) outcome above.
                from app.models.agent import AgentExecution, AgentExecutionMode, AgentExecutionStatus

                execution = await session.get(AgentExecution, request.agent_execution_id)
                if execution is not None and execution.tenant_id == tenant_id:
                    if execution.mode == AgentExecutionMode.REASONING:
                        # Phase 5: a REASONING-mode execution's status is
                        # NOT set here — the bounded reasoning loop owns
                        # that transition (it may CONTINUE to another
                        # step, not just COMPLETE/FAIL outright). Flag it
                        # so this method calls back into
                        # AgentReasoningService.resume_after_approval
                        # AFTER this transaction commits, never a second,
                        # parallel tool-execution path — the tool above
                        # already ran through the one governed
                        # ToolRegistry.execute() choke point.
                        continue_reasoning_execution_id = execution.id
                    else:
                        # Phase 7: SINGLE_ACTION now has its own durable
                        # step (written by AgentExecutionService.run_action
                        # before the approval gate was ever hit — see that
                        # module's docstring) — finish it from the exact
                        # same outcome the AgentExecution row itself is
                        # about to record, so a crash between this point
                        # and commit still leaves a step-level record
                        # AgentExecutionService.resume_recovered can find
                        # already terminal (never re-invoking the tool).
                        from app.models.agent import AgentExecutionStep, AgentExecutionStepStatus

                        step = (
                            await session.execute(
                                select(AgentExecutionStep).where(
                                    AgentExecutionStep.execution_id == execution.id,
                                    AgentExecutionStep.step_number == 1,
                                )
                            )
                        ).scalar_one_or_none()
                        if step is not None:
                            step.status = (
                                AgentExecutionStepStatus.EXECUTED if success else AgentExecutionStepStatus.FAILED
                            )
                            if success:
                                step.output_summary = result
                            else:
                                step.error_code = (error or "tool_execution_error")[:100]
                            step.completed_at = now

                        execution.status = (
                            AgentExecutionStatus.COMPLETED if success else AgentExecutionStatus.FAILED
                        )
                        execution.result_summary = result
                        execution.error_message = error
                        execution.completed_at = now
            await session.commit()
            await session.refresh(request)
            snapshot = _detached_copy(request)

        event_type = EventType.APPROVAL_EXECUTION_COMPLETED if success else EventType.APPROVAL_EXECUTION_FAILED
        await self._publish(
            tenant_id, event_type, approval_id,
            {"tool_name": snapshot.tool_name, "error": error}, correlation_id=correlation_id,
        )
        await self._audit(
            tenant_id, snapshot.decided_by, "approval.execution.completed" if success else "approval.execution.failed",
            approval_id=approval_id, correlation_id=correlation_id,
            result="success" if success else "failure", error=error,
        )

        if continue_reasoning_execution_id is not None:
            # Phase 5: resume the bounded reasoning loop with the outcome
            # of the tool call ToolRegistry.execute() already ran above —
            # this NEVER re-executes the tool, only records the outcome as
            # this step's observation and continues (or halts) the loop.
            # Lazy import / lazy construction avoids a module-load-time
            # circular import (agent_reasoning_service.py doesn't import
            # this module, but keeping the wiring lazy here matches this
            # file's existing convention for every other Phase 4/5 agent
            # cross-reference — see the `from app.models.agent import ...`
            # imports above).
            from app.services.agent_execution_service import AgentExecutionService
            from app.services.agent_reasoning_service import AgentReasoningService

            reasoning = AgentReasoningService(
                self._session_factory, self._registry, AgentExecutionService(self._session_factory, self._registry),
                ai_provider=self._reasoning_ai_provider,
            )
            await reasoning.resume_after_approval(
                tenant_id, continue_reasoning_execution_id,
                tool_success=success, tool_result=result, tool_error=error,
            )

        return snapshot

    async def _reconstruct_context(self, tenant_id: uuid.UUID, request: ApprovalRequest) -> ExecutionContext:
        role: Role | None = None
        if request.requested_by_role:
            role = Role(request.requested_by_role)
        elif request.requested_by_type == ActorType.USER and request.requested_by_id is not None:
            # Legacy rows (created before Phase 9, or via a call site that
            # doesn't yet thread a role through — see approval_helper.py)
            # fall back to the requester's *current* role rather than
            # guessing or defaulting to something permissive.
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                user = await session.get(User, request.requested_by_id)
                if user is not None:
                    role = Role(user.role)
        return ExecutionContext(
            tenant_id=tenant_id,
            actor_type=request.requested_by_type,
            actor_id=request.requested_by_id,
            role=role,
            correlation_id=request.correlation_id,
            # Phase 4: only ever set on rows created by an Agent-actor call
            # (see approval.py's Phase 4 column comment / registry.py's
            # _create_approval_request) — None for every pre-existing row
            # and every non-Agent actor, matching every other field here.
            agent_id=request.agent_id,
            agent_version_id=request.agent_version_id,
        )

    async def _learn_from_ai_decision(
        self, tenant_id: uuid.UUID, request: ApprovalRequest, *, decision: str, decision_note: str | None
    ) -> None:
        """Phase 19 (Learn): the ONLY place a human approve/reject decision
        becomes governed Company Memory feedback. Rule 6: only an
        AI-originated ApprovalRequest produces feedback — an AUTO
        execution never creates an ApprovalRequest at all, so it can never
        reach here, and a human-requested approval (requested_by_type ==
        USER) is not an AI decision to learn from.

        Rule 15: this is NOT the AI deciding what to remember — the human
        approve/reject click is the only trigger, already durably decided
        by the real `ApprovalStatus` CAS in `approve()`/`reject()` before
        this is ever called; this method only describes that already-made
        decision. Rule 2: never invents a reason the owner didn't give —
        `decision_note` is used verbatim if present, otherwise the memory
        honestly says none was given, never a fabricated explanation.
        Rule 12: idempotent for free — the CAS above guarantees this is
        reached at most once per ApprovalRequest, so `key` (derived
        deterministically from `request.id`) is proposed at most once.
        A failure here must never undo or block the real approval/
        rejection decision that already committed — logged, not raised.
        """
        if request.requested_by_type != ActorType.AI:
            return

        value = (
            f"Owner {decision} an AI-proposed action: tool='{request.tool_name}', "
            f"arguments={json.dumps(request.tool_input, sort_keys=True, default=str)}."
        )
        value += f" Owner note: {decision_note!r}." if decision_note else " No reason was given."
        if len(value) > MAX_VALUE_LENGTH:
            value = value[: MAX_VALUE_LENGTH - 1] + "…"

        try:
            await self._memory.propose_memory(
                tenant_id,
                memory_type=MemoryType.AI_FEEDBACK,
                key=f"ai_feedback_approval_{request.id.hex}",
                value=value,
                description=None,
                source_entity_type="approval_request",
                source_entity_id=request.id,
                reason="derived from an owner decision on an AI Next Action proposal",
            )
        except MemoryValidationError as exc:  # noqa: BLE001 — learning is best-effort, never blocks the real decision
            logger.warning(
                "ai_feedback_memory_proposal_failed", approval_id=str(request.id), tenant_id=str(tenant_id), error=str(exc)
            )

    async def _publish(
        self, tenant_id: uuid.UUID, event_type: str, approval_id: uuid.UUID, payload: dict, *, correlation_id: uuid.UUID | None
    ) -> None:
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=event_type,
            source="approval_execution_service",
            entity_type="approval_request",
            entity_id=approval_id,
            payload=payload,
            correlation_id=correlation_id,
        )

    async def _audit(
        self,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        action: str,
        *,
        approval_id: uuid.UUID,
        correlation_id: uuid.UUID | None,
        result: str,
        error: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action=action,
                    tool=None,
                    entity_type="approval_request",
                    entity_id=approval_id,
                    input_summary={"error": error} if error else None,
                    result=result,
                    approval_id=approval_id,
                    correlation_id=correlation_id,
                )
            )
            await session.commit()


def _detached_copy(request: ApprovalRequest) -> ApprovalRequest:
    """A plain, session-independent snapshot of the fields callers need —
    avoids `DetachedInstanceError` after the session that loaded `request`
    has closed."""
    copy = ApprovalRequest(
        tenant_id=request.tenant_id,
        requested_by_type=request.requested_by_type,
        requested_by_id=request.requested_by_id,
        requested_by_role=request.requested_by_role,
        tool_name=request.tool_name,
        action_type=request.action_type,
        reason=request.reason,
        tool_input=request.tool_input,
        status=request.status,
        decided_by=request.decided_by,
        decision_note=request.decision_note,
        correlation_id=request.correlation_id,
        decided_at=request.decided_at,
        execution_status=request.execution_status,
        execution_result=request.execution_result,
        execution_error=request.execution_error,
        executed_at=request.executed_at,
        execution_attempts=request.execution_attempts,
        idempotency_key=request.idempotency_key,
        # Phase 4 columns — omitting these here would silently drop the
        # agent-identity context every resumed call needs (discovered via
        # a real, failing test: an Agent-initiated approval's resume
        # attempt raised "missing agent identity" because this snapshot
        # helper predates these columns and wasn't taught about them).
        agent_id=request.agent_id,
        agent_version_id=request.agent_version_id,
        agent_execution_id=request.agent_execution_id,
    )
    copy.id = request.id
    copy.created_at = request.created_at
    copy.updated_at = request.updated_at
    return copy
