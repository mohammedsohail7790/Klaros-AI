"""Phase 4 (Agent Runtime foundation): `Agent` / `AgentVersion` /
`AgentToolPermission` / `AgentExecution` — the governed execution layer
described in KLAROS_FINAL_AGENT_MODEL.md and KLAROS_FINAL_SECURITY_MODEL.md.

This is NOT a second execution engine. Every `AgentExecution` is a single
governed call through the existing, unchanged `ToolRegistry.execute()`
pipeline (`app/tools/registry.py`), extended with two new pre-checks
(agent tool permission, agent autonomy ceiling — see registry.py) that are
inserted into that single choke point, never a parallel one. There is no
`while agent_thinks: ...` loop here — an `AgentExecution` requests exactly
one declared tool call; a general autonomous planning loop is explicitly
out of scope for this phase (KLAROS_DO_NOT_BUILD_YET.md's spirit, and this
phase's own instructions).

Reconciliation notes (see PHASE_4_IMPLEMENTATION_LOG.md for the full
writeup):

  - `Organization.autonomy_level` (app/models/organization.py) is NOT
    touched, NOT read, and NOT resurrected anywhere in this file or its
    service layer. Autonomy is enforced here, at the Agent level, via
    `AgentAutonomyTier` — exactly the "replacement architecture" called
    for in KLAROS_FINAL_SECURITY_MODEL.md §B.
  - `AgentToolPermission` is the LIVE, mutable, deny-by-default grant table
    for an Agent (KLAROS_FINAL_AGENT_MODEL.md: "join table: agent_id,
    tool_name, optional constraint"). `AgentVersion.tool_permissions_
    snapshot` is a denormalized, immutable COPY of those grants taken at
    publish time — the doc's own words: "denormalized copy of
    AgentToolPermission at publish time, for reproducibility even if
    permissions later change". Execution-time authorization checks the
    immutable snapshot on the exact `AgentVersion` an execution runs
    against, never the live table — this is what makes "Version
    Immutability" (a published version's executable configuration never
    changes) hold even as an operator later grants/revokes live
    `AgentToolPermission` rows in preparation for the *next* version.
  - `AgentExecution` records a single governed tool call (see module
    docstring above) — `tool_name`/`tool_input` capture exactly what was
    requested, `result_summary` captures a redacted, non-secret summary of
    the outcome (never raw provider output, matching
    app/tools/redact.py's existing redaction contract used by
    ToolRegistry's own audit rows).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class AgentStatus(StrEnum):
    """KLAROS_FINAL_AGENT_MODEL.md "Lifecycle": DRAFT (editable, not
    executable) -> ACTIVE (executable, versions immutable once run) ->
    PAUSED (existing runs may complete, no NEW runs) -> ARCHIVED
    (read-only, historical). See agent_service.py for the enforced
    transition table — an archived or paused agent must never execute."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class AgentVersionStatus(StrEnum):
    """KLAROS_FINAL_AGENT_MODEL.md §Agent model — full specification."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


class AgentAutonomyTier(StrEnum):
    """The four tiers named in KLAROS_FINAL_SECURITY_MODEL.md §B and
    KLAROS_FINAL_AGENT_MODEL.md's governance chain step 5 ("Observe/
    Recommend/Execute-with-approval/Execute-autonomous"). Exact enforced
    semantics (documented in full in agent_execution_service.py, since
    neither source document specifies the per-tier mechanics — this is
    this implementation's own, explicitly reconciled, addition):

      OBSERVE / RECOMMEND -> tool execution is never permitted through
        this Agent at all (ceiling = BLOCKED, composed with the tool's own
        policy — never widens it, matching "this check can only narrow").
        The two tiers are kept distinct in the enum (for a future phase
        that lets an OBSERVE/RECOMMEND-tier agent *propose* a
        Recommendation-shaped output without ever touching ToolRegistry)
        but enforce identically in this phase, since no such
        recommendation-only output path is built here.
      EXECUTE_WITH_APPROVAL -> every tool call this Agent makes is forced
        to at least APPROVAL_REQUIRED, regardless of the tool's own
        (possibly AUTO) policy — the agent can act, but only with an
        explicit human confirmation of each individual action.
      EXECUTE_AUTONOMOUS -> the tool's own ActionPolicy governs, unmodified
        (pass-through ceiling = AUTO) — but this NEVER bypasses a tool
        policy of APPROVAL_REQUIRED or BLOCKED, and NEVER bypasses
        `SYSTEM_BLOCKED_TOOLS` or the org-wide `ai_paused` kill switch,
        exactly as KLAROS_FINAL_AGENT_MODEL.md requires ("financial_risk-
        tagged tools and the existing SYSTEM_BLOCKED_TOOLS floor are never
        bypassable by any tier").
    """

    OBSERVE = "OBSERVE"
    RECOMMEND = "RECOMMEND"
    EXECUTE_WITH_APPROVAL = "EXECUTE_WITH_APPROVAL"
    EXECUTE_AUTONOMOUS = "EXECUTE_AUTONOMOUS"


class AgentTriggerSource(StrEnum):
    """KLAROS_FINAL_AGENT_MODEL.md: `AgentExecution.trigger_source`
    (manual/scheduled/event). Only MANUAL is reachable through the API in
    this phase (POST /agents/{id}/execute) — SCHEDULED/EVENT are modeled
    now (so AgentExecution rows are forward-compatible) but nothing in
    this phase creates one; wiring a scheduler or event-bus subscription
    to actually trigger a SCHEDULED/EVENT run is explicitly deferred (see
    PHASE_4_IMPLEMENTATION_LOG.md "Explicitly deferred work")."""

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    EVENT = "EVENT"


class AgentExecutionStatus(StrEnum):
    """KLAROS_FINAL_AGENT_MODEL.md names RUNNING/COMPLETED/FAILED/HALTED.
    This implementation adds PENDING (the row exists, governance checks
    are in flight, before the tool actually runs) and WAITING_APPROVAL
    (the governed call resolved to APPROVAL_REQUIRED and is durably
    parked on the existing `ApprovalRequest`) — both are additive,
    reconciled states needed to make the approval-integration requirement
    concretely testable; see PHASE_4_IMPLEMENTATION_LOG.md."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HALTED = "HALTED"


class AgentExecutionMode(StrEnum):
    """Phase 5 (KLAROS Agent Runtime — bounded reasoning loop). Distinguishes
    the Phase 4 governed single-action executor (`AgentExecutionService.
    run_action`, unchanged) from the Phase 5 bounded LLM-driven multi-step
    loop (`AgentReasoningService.start`). Additive: every Phase 4 row is
    implicitly SINGLE_ACTION (the column default), so no existing behavior,
    test, or row changes meaning. See agent_reasoning_service.py's module
    docstring for the full runtime architecture this mode gates."""

    SINGLE_ACTION = "SINGLE_ACTION"
    REASONING = "REASONING"


class AgentExecutionStepType(StrEnum):
    """One `AgentExecutionStep` row per governed decision the reasoning loop
    made — see agent_reasoning_service.py's reconciliation note on
    tool-chain-depth semantics ("one step" == one attempted tool
    invocation, whether it ultimately succeeds, fails, or requires
    approval)."""

    TOOL_CALL = "TOOL_CALL"
    COMPLETE = "COMPLETE"


class AgentExecutionStepStatus(StrEnum):
    """The outcome of one reasoning-loop step. EXECUTED covers both a
    successful TOOL_CALL and a COMPLETE decision (there is no further
    "success" distinction needed for a completion). APPROVAL_REQUIRED means
    the loop is currently parked on this step, pending a human decision —
    at most one step per execution may be in this state at a time (the loop
    never proposes a second action while one is pending)."""

    EXECUTED = "EXECUTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    DENIED = "DENIED"


class AgentExecutionTerminationReason(StrEnum):
    """Every way a Phase 5 REASONING-mode `AgentExecution` can stop —
    persisted on `AgentExecution.termination_reason` so "why did it stop"
    is always answerable from the durable record, never only inferable
    from an exception that already unwound. See agent_reasoning_service.py
    for exactly which runtime condition maps to which of these."""

    COMPLETED_BY_MODEL = "COMPLETED_BY_MODEL"
    MAX_TOOL_CHAIN_DEPTH = "MAX_TOOL_CHAIN_DEPTH"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    AI_CALL_FAILED = "AI_CALL_FAILED"
    MALFORMED_LLM_OUTPUT = "MALFORMED_LLM_OUTPUT"
    UNKNOWN_TOOL_PROPOSED = "UNKNOWN_TOOL_PROPOSED"
    TOOL_GOVERNANCE_REJECTED = "TOOL_GOVERNANCE_REJECTED"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    # Phase 6 (crash recovery) additions — see agent_recovery_service.py.
    RECOVERY_ATTEMPTS_EXHAUSTED = "RECOVERY_ATTEMPTS_EXHAUSTED"
    # Phase 6 left every SINGLE_ACTION execution unresumable (no durable
    # step boundary existed yet) — this value is preserved for historical
    # rows and for the defensive fallback branch in
    # AgentRecoveryService._recover_one, but Phase 7's step-level
    # durability (see AgentExecutionService.resume_recovered) means it is
    # no longer the normal outcome of a SINGLE_ACTION recovery.
    RECOVERY_UNSAFE_SINGLE_ACTION = "RECOVERY_UNSAFE_SINGLE_ACTION"
    # Phase 7 (Agent Runtime Reliability II): a step was left mid-flight by
    # a crash (started, never completed) and the tool it was calling has no
    # verified idempotency support (Tool.supports_idempotency is False) —
    # per the "Unknown External Outcome" safety rule, the runtime never
    # guesses whether the external side effect happened; it safe-halts
    # instead, for a human to inspect and, if needed, manually re-issue.
    AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT = "AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT"


class Agent(TenantScopedMixin, Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AgentStatus.DRAFT, index=True)
    autonomy_tier: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AgentAutonomyTier.OBSERVE
    )
    # The Role this agent's ExecutionContext carries into ToolRegistry's
    # own (unchanged) RBAC check — see registry.py's docstring and
    # agent_execution_service.py's reconciliation note on why an Agent
    # actor needs a Role at all. Set at creation from the creator's own
    # Role (never client-supplied, never escalatable beyond it), editable
    # only while the agent is DRAFT.
    acting_role: Mapped[str] = mapped_column(String(30), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_versions.id", use_alter=True, name="fk_agents_current_version"),
        nullable=True,
    )
    # Historical traceability only (KLAROS prompt's "Business Blueprint
    # Relationship" section) — never a live join an Agent depends on at
    # execution time. Both nullable: an Agent need not have been created
    # from an accepted Recommendation/Blueprint.
    source_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=True
    )
    source_blueprint_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recommendations.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (Index("ix_agents_tenant_status", "tenant_id", "status"),)


class AgentVersion(TenantScopedMixin, Base):
    """Immutable once any `AgentExecution` references it — see
    agent_service.py::AgentService for the enforced invariant (no UPDATE
    path exists for a version's executable columns once
    `first_executed_at` is set; a new version must be created instead)."""

    __tablename__ = "agent_versions"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AgentVersionStatus.DRAFT, index=True
    )
    instructions_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # [{"tool_name": ..., "constraint": {...} | None}, ...] — see module
    # docstring's reconciliation note.
    tool_permissions_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    memory_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    triggers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_executions_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_concurrent_executions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_tool_chain_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Can only NARROW the tool's own ActionPolicy ceiling, never widen it
    # — enforced in agent_execution_service.py, never trusted blindly from
    # this column alone. None means "no additional narrowing beyond the
    # agent's autonomy_tier ceiling".
    approval_policy_override: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Set the first time any AgentExecution successfully references this
    # version — the concrete flag agent_service.py's mutation guard checks
    # (belt-and-suspenders alongside "never UPDATE a PUBLISHED/DEPRECATED
    # version's executable columns at all").
    first_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        Index("ix_agent_versions_tenant_agent", "tenant_id", "agent_id"),
    )


class AgentToolPermission(TenantScopedMixin, Base):
    """Deny-by-default grant table (KLAROS_FINAL_AGENT_MODEL.md: `agent_id`,
    `tool_name`, optional `constraint`). LIVE/mutable — see module
    docstring for why AgentVersion.tool_permissions_snapshot, not this
    table, is what execution-time checks actually consult."""

    __tablename__ = "agent_tool_permissions"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    constraint_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("agent_id", "tool_name", name="uq_agent_tool_permissions_agent_tool"),
        Index("ix_agent_tool_permissions_tenant_agent", "tenant_id", "agent_id"),
    )


class AgentExecution(TenantScopedMixin, Base):
    """The durable execution/audit record for a single governed tool call
    made by an Agent. Never stores secrets/credentials/raw unredacted
    provider output — `tool_input_summary`/`result_summary` mirror
    `app/tools/redact.py`'s redaction contract, the same one
    `ToolRegistry._audit` already uses for its own `AuditLog` rows."""

    __tablename__ = "agent_executions"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_versions.id"), nullable=False, index=True
    )
    trigger_source: Mapped[str] = mapped_column(String(20), nullable=False, default=AgentTriggerSource.MANUAL)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AgentExecutionStatus.PENDING, index=True
    )
    # Phase 5: SINGLE_ACTION (Phase 4's one-declared-tool-call executor,
    # the column default — every pre-Phase-5 row is implicitly this) vs.
    # REASONING (the new bounded LLM-driven multi-step loop). See
    # agent_reasoning_service.py.
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default=AgentExecutionMode.SINGLE_ACTION)
    # SINGLE_ACTION: the one tool this execution ran (caller-declared).
    # REASONING: the tool the loop is *currently* on (nullable until the
    # first step runs) — the full per-step tool history lives on
    # AgentExecutionStep, not here.
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Phase 5 (REASONING mode only): the caller-supplied objective handed to
    # the LLM as the reasoning goal. Untrusted free text — always fenced as
    # DATA in the prompt, never concatenated into system instructions. NULL
    # for SINGLE_ACTION executions.
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 5 (REASONING mode only): the model's own final COMPLETE
    # response, capped/validated by AgentDecision — never raw
    # chain-of-thought, never tool secrets (nothing here is sourced from
    # tool output directly).
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 5 (REASONING mode only): why the loop stopped — always set
    # whenever a REASONING execution reaches a terminal state. See
    # AgentExecutionTerminationReason.
    termination_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Phase 5 (REASONING mode only): the loop's own bounded continuation
    # state — a small, redacted, size-capped summary of prior steps (never
    # raw tool output, never secrets, never hidden chain-of-thought) needed
    # to resume the loop after an APPROVAL_REQUIRED pause. See
    # agent_reasoning_service.py::_ReasoningState.
    reasoning_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Phase 5 (REASONING mode only): how many steps (bounded by
    # AgentVersion.max_tool_chain_depth) have been attempted so far.
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approval_requests.id"), nullable=True
    )
    # Idempotency contract (this phase's "Execution Safety" requirement):
    # a caller-supplied key, unique per tenant+agent when present, so a
    # retried POST /agents/{id}/execute never double-runs the same
    # action. NULL means "no idempotency key supplied" — allowed (not
    # every trigger source can supply one), and multiple NULLs are not
    # deduplicated (standard partial-unique-index semantics, matched at
    # the DB level in the migration).
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Phase 6 (Agent Runtime Reliability — crash recovery/durable
    # execution ownership). All four columns are additive/nullable-or-
    # defaulted so every pre-Phase-6 row is unaffected. See
    # agent_recovery_service.py's module docstring for the full design;
    # summary here:
    #   execution_owner_id: a random UUID a process generates once at
    #     startup, written whenever it marks this execution RUNNING
    #     (normal start/resume) or successfully claims it as stale
    #     (recovery sweep). Never set/renewed by any process that isn't
    #     the current owner (see AgentReasoningService's heartbeat, which
    #     is a conditional UPDATE ... WHERE execution_owner_id = self —
    #     a process can never extend another owner's lease).
    #   lease_expires_at: the durable, DB-level "is this execution still
    #     genuinely alive" signal recovery sweeps compare against `now()`
    #     — the actual stale-detection mechanism (never an in-memory
    #     timer/lock). NULL is treated identically to "already expired"
    #     (so any pre-Phase-6-created row, or a row somehow created
    #     without a lease, is still a legal recovery candidate).
    #   heartbeat_at: renewed alongside lease_expires_at, kept mainly for
    #     observability ("when was this execution last known alive") —
    #     recovery decisions themselves key off lease_expires_at.
    #   recovery_attempt_count: bounds "crash -> recover -> crash again"
    #     (the poison-execution case) — once it exceeds
    #     AgentRecoveryService.MAX_RECOVERY_ATTEMPTS the sweep halts the
    #     execution permanently instead of claiming it again.
    execution_owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_agent_executions_tenant_agent", "tenant_id", "agent_id"),
        Index("ix_agent_executions_tenant_agent_version", "tenant_id", "agent_version_id"),
        Index("ix_agent_executions_tenant_status", "tenant_id", "status"),
        Index("ix_agent_executions_status_lease", "status", "lease_expires_at"),
        UniqueConstraint(
            "tenant_id", "agent_id", "idempotency_key", name="uq_agent_executions_tenant_agent_idempotency"
        ),
    )


class AgentExecutionStep(TenantScopedMixin, Base):
    """Phase 5: one durable row per governed decision the bounded reasoning
    loop made for a REASONING-mode `AgentExecution` — the concrete
    "execution trace" the architecture requires (agent_reasoning_service.py
    module docstring has the full runtime flow). Never stores raw
    chain-of-thought (`decision_summary` is the model's own short, safe
    rationale, already length-capped by `AgentDecision.reasoning_summary`'s
    validation — see agent_reasoning_service.py) and never stores secrets
    (`input_summary`/`output_summary` both go through the same
    `app/tools/redact.py::redact_input` contract `ToolRegistry._audit`
    already uses).

    Phase 7 (Agent Runtime Reliability II): this same table/model is reused,
    unmodified, as the durable step boundary for SINGLE_ACTION-mode
    executions too (see AgentExecutionService's own step-writing methods) —
    always exactly one row, `step_number=1`, `step_type=TOOL_CALL`. No new
    model/table was introduced for this: a SINGLE_ACTION execution is, by
    construction, a REASONING execution bounded to a single step, and both
    now share the identical durability semantics (row exists before the
    tool call is attempted; `completed_at IS NULL` is the durable "still
    mid-flight" signal both AgentReasoningService._reconcile_interrupted_
    step and AgentExecutionService.resume_recovered key off of)."""

    __tablename__ = "agent_execution_steps"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id"), nullable=False, index=True
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # The model's own safe, high-level rationale for this decision — NEVER
    # unrestricted hidden chain-of-thought. Capped to a small length by
    # AgentDecision's own validation before it ever reaches this column.
    decision_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("execution_id", "step_number", name="uq_agent_execution_steps_execution_step"),
        Index("ix_agent_execution_steps_tenant_execution", "tenant_id", "execution_id"),
    )
