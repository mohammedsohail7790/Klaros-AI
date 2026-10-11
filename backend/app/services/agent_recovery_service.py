"""Phase 6 (Agent Runtime Reliability): crash recovery for interrupted
REASONING-mode `AgentExecution` rows.

This is NOT a second execution engine. A recovered execution resumes
through the exact same `AgentReasoningService._run_loop` every other
continuation (start/approval-resume) already uses — see that module's own
Phase 6 docstring addition. This service's only job is the thing
`AgentReasoningService` cannot safely do for itself: notice that a
*different* process's execution appears to have died mid-loop, atomically
claim ownership of it so no other process can claim it too, and re-enter
the loop.

Design summary (full reasoning in PHASE_6_IMPLEMENTATION_LOG.md):

  - **Ownership/lease**: `AgentExecution.execution_owner_id` /
    `lease_expires_at` / `heartbeat_at` (additive Phase 6 columns — see
    app/models/agent.py). A lease is claimed via a single atomic
    conditional `UPDATE ... WHERE status='RUNNING' AND (lease_expires_at
    IS NULL OR lease_expires_at < now()) SET execution_owner_id=<new>,
    lease_expires_at=<now+LEASE>, ...`, checked via `result.rowcount`.
    This is the exact same compare-and-swap pattern already proven in
    `ApprovalExecutionService`'s `execution_status` CAS (see that module's
    `rowcount == 1` claim check) — a single UPDATE statement is atomic
    under Postgres's row-level locking, so two concurrent claims of the
    same row can never both succeed; no explicit `SELECT ... FOR UPDATE`
    or advisory lock is needed on top of it.

  - **Stale detection**: purely lease-expiration-based — there is no
    separate STALE/RECOVERING persistent status. A RUNNING execution
    whose lease has expired (or was never set) IS the "possibly crashed"
    signal; nothing else is needed, and it keeps the execution's status
    enum exactly as Phase 4/5 left it (fewer states, per this phase's own
    "prefer fewer states" instruction).

  - **What's eligible**: only `status == RUNNING`. `WAITING_APPROVAL` is
    never a sweep candidate (a different, already-correct resume boundary
    owned entirely by `ApprovalExecutionService` — see that module and
    `agent_reasoning_service.py`'s approval docstring); `COMPLETED`/
    `FAILED`/`HALTED` are terminal and excluded by construction (the
    `status == RUNNING` filter alone guarantees this, no extra check
    needed).

  - **REASONING vs SINGLE_ACTION**: both modes now have durable,
    step-level checkpoints (`AgentExecutionStep`) as of Phase 7 (Agent
    Runtime Reliability II) — see `AgentExecutionService.resume_recovered`
    for the SINGLE_ACTION decision table (mirrors
    `AgentReasoningService._reconcile_interrupted_step`/`resume_recovered`
    for REASONING). Both are resumed through their own service's
    `resume_recovered` entry point, never through a second execution
    engine. Before Phase 7, a SINGLE_ACTION execution (Phase 4's
    `AgentExecutionService.run_action`) had NO step-level boundary, so this
    service always safe-halted it (`RECOVERY_UNSAFE_SINGLE_ACTION`) rather
    than guess whether the tool had already run — a real, honestly
    documented limitation (see PHASE_6_IMPLEMENTATION_LOG.md §14). Phase 7
    closes that gap by giving SINGLE_ACTION the same durable step
    `run_action` now writes; `RECOVERY_UNSAFE_SINGLE_ACTION` is kept only
    as the defensive fallback for the (should-be-impossible) case of a
    SINGLE_ACTION execution whose `AgentExecutionService.resume_recovered`
    call itself raises before reaching a terminal state.

  - **Poison-execution bound**: `recovery_attempt_count` (incremented by
    every successful claim, whether or not the resume itself later
    succeeds) is compared against `MAX_RECOVERY_ATTEMPTS`; once exceeded,
    the execution is halted (`RECOVERY_ATTEMPTS_EXHAUSTED`) instead of
    claimed again — bounds a "crash -> recover -> crash again" loop to a
    small, finite number of attempts, never infinite.

  - **Kill switch during recovery**: no special-case code here at all —
    the resumed `_run_loop` calls the exact same `ToolRegistry.execute()`
    every other path calls, which re-checks `Organization.ai_paused` fresh
    on every single tool call (existing Phase 4 code, unmodified). If the
    kill switch is on, the next tool call is denied exactly as it would be
    for any other in-flight execution — recovery gets no special
    exemption and needs none.

  - **Trigger mechanism**: this service is invoked via `sweep_once()`,
    wired as another `on_tick` hook on the existing `EventWorker` (see
    app/events/worker.py's `combine_on_tick`) — the same mechanism
    `AutomationService.check_and_dispatch_scheduled` and Morning Brief's
    scheduler already piggyback on. No new poller, no Temporal Schedule
    (see PHASE_6_IMPLEMENTATION_LOG.md's Temporal-usage reconciliation).

Phase 8 (Agent Runtime Reliability III) addition — see
PHASE_8_IMPLEMENTATION_LOG.md for the full design. Summary:

  - **PENDING orphan recovery**: both `AgentExecutionService.run_action`
    and `AgentReasoningService.start` write `AgentExecution` in two
    separate commits — first `INSERT ... status=PENDING`, then a second
    `UPDATE ... status=RUNNING, execution_owner_id=..., lease_expires_at=...`
    (the lease claim). If the process dies between those two commits, the
    row is durably left `PENDING` forever — `_find_stale_candidates`
    previously only looked at `status == RUNNING`, so this row was never
    swept by anything (Phase 8's primary objective gap #1).
  - **Why this is provably safe to recover, not a guess**: by
    construction, in BOTH modes, no `AgentExecutionStep` row is ever
    written and no tool is ever invoked before the RUNNING-transition
    commit lands (`run_action` writes its step only after `_mark_running`
    returns; `AgentReasoningService._run_loop` — which is what performs
    every tool call — is only ever entered after `start()`'s own
    RUNNING-transition commit). Therefore a row that is STILL `PENDING`
    is architecturally proof, not inference, that no external side effect
    could have occurred yet — there is nothing to reconcile, unlike the
    genuinely ambiguous mid-flight RUNNING case. This is the one crash
    boundary in the whole lifecycle where "nothing happened" is provable
    rather than assumed.
  - **No new lifecycle state, no new column, no second recovery engine**:
    a stale `PENDING` row is claimed with the exact same atomic
    conditional-`UPDATE`-by-`rowcount` pattern already used for stale
    `RUNNING` rows (see `_claim` below — extended, not duplicated), then
    handed to the SAME `resume_recovered` entry point every other
    recovery already uses. Because no step can exist yet, both
    `AgentExecutionService.resume_recovered` and
    `AgentReasoningService.resume_recovered`'s existing "no step row ->
    create it and run the tool/loop exactly once" branch already handles
    this correctly, unmodified.
  - **Staleness signal for PENDING**: a `PENDING` row has no lease (one
    is only ever assigned on the RUNNING transition), so elapsed time
    since `created_at` is the only available signal —
    `PENDING_ORPHAN_THRESHOLD` (deliberately reused: it is the same value
    as `EXECUTION_LEASE_DURATION`, not a new independent tunable). In
    normal operation the gap between the two commits is sub-millisecond;
    a genuinely live request taking 5 minutes between them would itself
    already be an anomaly indistinguishable from a hang, so this bound
    does not race a healthy in-flight request in practice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import discovery_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.agent import (
    AgentExecution,
    AgentExecutionMode,
    AgentExecutionStatus,
    AgentExecutionTerminationReason,
)
from app.models.audit_log import AuditLog

logger = structlog.get_logger(__name__)

# Phase 6: the one centralized lease-duration constant this service's own
# claim uses — deliberately identical to (imported from, not duplicated)
# AgentReasoningService's own EXECUTION_LEASE_DURATION, so a
# recovery-claimed execution gets the same grace window a normally-running
# one would.
from app.services.agent_execution_service import EXECUTION_LEASE_DURATION, AgentExecutionService  # noqa: E402
from app.services.agent_reasoning_service import (  # noqa: E402  (after other imports, avoids a cycle at module top)
    AgentReasoningService,
)

# Bounded recovery: after this many successful claims of the same
# execution, stop trying — a poison execution (one that crashes at the
# same point every time) is halted permanently instead of retried forever.
MAX_RECOVERY_ATTEMPTS = 5

# Phase 8: how long a PENDING execution may sit without reaching RUNNING
# before it is considered orphaned (see module docstring's Phase 8
# section). Deliberately identical to EXECUTION_LEASE_DURATION — reused,
# not a new independent tunable.
PENDING_ORPHAN_THRESHOLD = EXECUTION_LEASE_DURATION


class AgentRecoveryService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        reasoning_service: AgentReasoningService,
        *,
        worker_id: uuid.UUID | None = None,
        execution_service: AgentExecutionService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._reasoning = reasoning_service
        # Phase 7: defaults to the exact AgentExecutionService instance
        # `reasoning_service` already holds (see that class's
        # `execution_service` property) — never a second, independently
        # constructed one, so both SINGLE_ACTION and REASONING recovery
        # share one wired instance unless a caller explicitly wants a
        # distinct one (e.g. a distinct worker_id/lease-owner for testing).
        self._exec = execution_service or reasoning_service.execution_service
        self._worker_id = worker_id or uuid.uuid4()

    async def sweep_once(
        self,
        tenant_id: uuid.UUID | None = None,
        *,
        limit: int = 10,
        now_utc: datetime | None = None,
    ) -> list[uuid.UUID]:
        """Find stale RUNNING executions (and, as of Phase 8, orphaned
        PENDING executions — see module docstring), atomically claim each
        one this worker wins the race for, and recover it. Returns the ids
        this worker successfully claimed (recovered or safely halted) —
        mainly for tests. Safe to call concurrently with itself (from
        another process/worker_id): the claim step is what decides who, if
        anyone, proceeds for a given execution id."""
        now_utc = now_utc or datetime.now(timezone.utc)
        candidates = await self._find_stale_candidates(tenant_id, limit, now_utc)
        claimed: list[uuid.UUID] = []
        for candidate_tenant_id, execution_id in candidates:
            won = await self._claim(candidate_tenant_id, execution_id, now_utc)
            if not won:
                continue
            await self._recover_one(candidate_tenant_id, execution_id)
            claimed.append(execution_id)
        return claimed

    async def _find_stale_candidates(
        self, tenant_id: uuid.UUID | None, limit: int, now_utc: datetime
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        # Real-RLS-enforcement fix (found via live staging validation,
        # round 8 of the staging-readiness task — same bug class as
        # team_service.py's invite-accept fix and
        # agent_execution_service.py's execution-lifecycle fix): the
        # `tenant_id is None` branch below is this worker's real,
        # actually-wired sweep-all-tenants tick (`sweep_once` is called
        # with no arguments from both app/main.py's background task and
        # app/events/worker.py's periodic jobs — never dead code). It
        # queries `self._session_factory()`, which is the ordinary
        # restricted `klaros_app` role connection — genuinely tenant-
        # scoped by RLS. With no `set_tenant_context` call possible (there
        # is no single tenant_id to stamp for a cross-tenant scan), every
        # row was invisible under real RLS enforcement: this method always
        # returned an empty list, meaning the entire crash-recovery safety
        # net never found anything to recover in a real RLS-enforcing
        # environment. Confirmed directly against real Postgres: a bare
        # `SELECT id, tenant_id FROM agent_executions` as `klaros_app` with
        # no context set returns zero rows.
        #
        # Fix: when this is genuinely a cross-tenant sweep (`tenant_id is
        # None`), read through the narrow, read-only `klaros_discovery`
        # role instead — which has exactly this `agent_executions.
        # discovery_select` policy (`USING (true)`, SELECT-only) for
        # exactly this purpose (same role, same precedent pattern already
        # used by `McpCredentialService._resolve_tenant_id_via_discovery`
        # — see that method's docstring for why a SEPARATE connection/role
        # is used rather than ever widening `klaros_app`'s own policy).
        # `discovery_select` is read-only, so every subsequent write
        # (`_claim`/`_recover_one`/`_halt`/`_audit`) still goes through the
        # normal restricted-role session with that row's own real
        # `tenant_id` stamped via `set_tenant_context` BEFORE the lookup —
        # this method now returns `(tenant_id, execution_id)` pairs so
        # every downstream call already has a real, known tenant_id and
        # never has to rediscover it via a context-less, RLS-gated query
        # (the exact root cause of this whole bug class). When a real
        # `tenant_id` is already given (the ordinary per-tenant call
        # shape), behavior is unchanged other than also returning it
        # paired with each id.
        if tenant_id is None and discovery_session_maker is None:
            # Fails closed exactly like McpCredentialService's own
            # documented fallback: no discovery role configured in this
            # environment means the cross-tenant sweep finds nothing,
            # which is the same (pre-existing, already-acceptable) outcome
            # as before this fix — never silently promoted to a dangerous
            # unscoped query against the restricted role.
            return []

        session_factory = (
            discovery_session_maker if tenant_id is None and discovery_session_maker is not None
            else self._session_factory
        )
        async with session_factory() as session:
            if tenant_id is not None:
                await set_tenant_context(session, tenant_id)
            pending_orphan_cutoff = now_utc - PENDING_ORPHAN_THRESHOLD
            query = (
                select(AgentExecution.tenant_id, AgentExecution.id)
                .where(
                    or_(
                        # Stale RUNNING — Phase 6's original candidate set.
                        and_(
                            AgentExecution.status == AgentExecutionStatus.RUNNING,
                            or_(
                                AgentExecution.lease_expires_at.is_(None),
                                AgentExecution.lease_expires_at < now_utc,
                            ),
                        ),
                        # Phase 8: orphaned PENDING — see module docstring.
                        # Provably never had a step/tool call, so it is
                        # always safe to claim and hand to the normal
                        # "no step row" resume path.
                        and_(
                            AgentExecution.status == AgentExecutionStatus.PENDING,
                            AgentExecution.created_at < pending_orphan_cutoff,
                        ),
                    )
                )
                .order_by(AgentExecution.created_at)
                .limit(limit)
            )
            if tenant_id is not None:
                query = query.where(AgentExecution.tenant_id == tenant_id)
            return [(row[0], row[1]) for row in (await session.execute(query)).all()]

    async def _claim(self, tenant_id: uuid.UUID, execution_id: uuid.UUID, now_utc: datetime) -> bool:
        """The one atomic operation this whole module exists for — see
        module docstring. A single conditional UPDATE, checked via
        rowcount; two concurrent callers racing on the same execution_id
        can never both get rowcount == 1. Phase 8: the WHERE clause now
        also matches an orphaned PENDING row (see module docstring); in
        both cases the row transitions to RUNNING with a freshly claimed
        lease, so the rest of the recovery path (`_recover_one` ->
        `resume_recovered`) is identical regardless of which branch
        matched.

        Real-RLS-enforcement fix (see `_find_stale_candidates`'s docstring
        above): this used to look up the owning tenant itself via
        `session.scalar(select(AgentExecution.tenant_id).where(...))` on
        the restricted `klaros_app` session with no context set yet —
        under real RLS that SELECT is just as invisible as any other
        (confirmed directly against real Postgres), so `owner_tenant_id`
        was always `None` and the UPDATE below always matched zero rows.
        `tenant_id` is now a real, already-known parameter (threaded from
        `_find_stale_candidates`'s discovery-role read), so it's stamped
        directly — no self-lookup needed."""
        pending_orphan_cutoff = now_utc - PENDING_ORPHAN_THRESHOLD
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(
                update(AgentExecution)
                .where(
                    AgentExecution.id == execution_id,
                    or_(
                        and_(
                            AgentExecution.status == AgentExecutionStatus.RUNNING,
                            or_(
                                AgentExecution.lease_expires_at.is_(None),
                                AgentExecution.lease_expires_at < now_utc,
                            ),
                        ),
                        and_(
                            AgentExecution.status == AgentExecutionStatus.PENDING,
                            AgentExecution.created_at < pending_orphan_cutoff,
                        ),
                    ),
                )
                .values(
                    status=AgentExecutionStatus.RUNNING,
                    started_at=func.coalesce(AgentExecution.started_at, now_utc),
                    execution_owner_id=self._worker_id,
                    lease_expires_at=now_utc + EXECUTION_LEASE_DURATION,
                    heartbeat_at=now_utc,
                    recovery_attempt_count=AgentExecution.recovery_attempt_count + 1,
                )
            )
            await session.commit()
            won = result.rowcount == 1
        if won:
            await self._audit(tenant_id, execution_id, "agent.execution.recovery.detected")
        return won

    async def _recover_one(self, tenant_id: uuid.UUID, execution_id: uuid.UUID) -> None:
        # Real-RLS-enforcement fix (see `_find_stale_candidates`'s
        # docstring above): this used to call `session.get` before
        # `set_tenant_context`, deriving the context to stamp from the row
        # it had just (always unsuccessfully, under real RLS) fetched.
        # `tenant_id` is now a real parameter passed in from `_claim`'s own
        # (already-fixed) caller.
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            execution = await session.get(AgentExecution, execution_id)
            mode = execution.mode
            attempt_count = execution.recovery_attempt_count

        if attempt_count > MAX_RECOVERY_ATTEMPTS:
            await self._halt(
                tenant_id, execution_id, AgentExecutionTerminationReason.RECOVERY_ATTEMPTS_EXHAUSTED,
                error=f"Execution exceeded {MAX_RECOVERY_ATTEMPTS} recovery attempts — halted, never retried again.",
            )
            await self._audit(
                tenant_id, execution_id, "agent.execution.recovery.failed", reason="recovery_attempts_exhausted"
            )
            return

        await self._audit(tenant_id, execution_id, "agent.execution.recovery.claimed")
        try:
            if mode == AgentExecutionMode.SINGLE_ACTION:
                # Phase 7: SINGLE_ACTION now has the exact same durable
                # step boundary REASONING does — see
                # AgentExecutionService.resume_recovered's own decision
                # table (module docstring above has the summary).
                await self._exec.resume_recovered(tenant_id, execution_id)
            else:
                await self._reasoning.resume_recovered(tenant_id, execution_id)
            await self._audit(tenant_id, execution_id, "agent.execution.recovery.resumed")
        except Exception as exc:  # noqa: BLE001 — one bad recovery must never crash the sweep or the worker tick
            logger.error("agent_recovery_resume_failed", execution_id=str(execution_id), error=str(exc))
            # Defensive fallback (should be unreachable in practice — see
            # module docstring): if resuming a SINGLE_ACTION execution
            # itself raises before reaching any terminal state, the
            # execution must never be left RUNNING/leased forever. Halt it
            # with the original Phase 6 reason, preserved exactly for this
            # fallback case.
            if mode == AgentExecutionMode.SINGLE_ACTION:
                await self._halt(
                    tenant_id, execution_id, AgentExecutionTerminationReason.RECOVERY_UNSAFE_SINGLE_ACTION,
                    error=f"SINGLE_ACTION recovery raised before reaching a terminal state: {exc}"[:2000],
                )
            await self._audit(tenant_id, execution_id, "agent.execution.recovery.failed", reason=str(exc)[:500])

    async def _halt(self, tenant_id, execution_id, termination_reason, *, error: str) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            execution = await session.get(AgentExecution, execution_id)
            execution.status = AgentExecutionStatus.FAILED
            execution.termination_reason = termination_reason
            execution.error_message = error[:2000]
            execution.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def _audit(
        self, tenant_id: uuid.UUID, execution_id: uuid.UUID, action: str, *, reason: str | None = None
    ) -> None:
        # Real-RLS-enforcement fix (see `_find_stale_candidates`'s
        # docstring above): this used to call `session.get` before
        # `set_tenant_context` and silently no-op when it returned `None`
        # — meaning this audit log was never actually written in a real
        # enforcing environment, with no error surfaced anywhere. All
        # callers now already have `tenant_id` in scope.
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            execution = await session.get(AgentExecution, execution_id)
            if execution is None:
                return
            session.add(
                AuditLog(
                    tenant_id=execution.tenant_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    action=action,
                    tool=None,
                    entity_type="agent_execution",
                    entity_id=execution.id,
                    input_summary={"reason": reason} if reason else None,
                    result="success",
                    correlation_id=execution.id,
                )
            )
            await session.commit()
