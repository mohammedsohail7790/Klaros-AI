import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalExecutionStatus(StrEnum):
    """Separate from `ApprovalStatus` (section 10/Phase 9): a decision
    (APPROVED/REJECTED) and whether the original action has actually run are
    different questions — this is exactly the distinction whose absence
    created the "approval dead end" named in every phase since 5."""

    NOT_STARTED = "NOT_STARTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class ApprovalRequest(TenantScopedMixin, Base):
    """section 10 / section 8: the persisted half of the approval boundary.

    Created whenever a tool execution resolves to APPROVAL_REQUIRED instead of
    running. As of Phase 9, approving one no longer just flips `status` —
    `ApprovalExecutionService` resumes and actually runs the original tool
    call through `ToolRegistry`, using the exact context the request was
    made in (`requested_by_type`/`requested_by_id`/`requested_by_role`,
    captured at creation time so resumption never has to guess at — or
    trust a client-supplied — identity).
    """

    __tablename__ = "approval_requests"

    requested_by_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ActorType
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Added Phase 9: the requester's Role at the moment of the request, so
    # execution can be resumed with the exact ExecutionContext the original
    # call would have had — never a client-supplied or re-derived role.
    # Nullable for rows created before this column existed.
    requested_by_role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ApprovalStatus.PENDING)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    # --- Added Phase 9: execution tracking, distinct from the approval decision itself ---
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ApprovalExecutionStatus.NOT_STARTED
    )
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    # Deterministic, non-client-supplied idempotency key for the execution
    # step specifically (distinct from the DB-level compare-and-swap on
    # execution_status, which is the actual concurrency guarantee — see
    # ApprovalExecutionService). Kept for traceability/audit correlation.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Added Phase 4 (Agent Runtime): additive, nullable columns only —
    # every pre-existing row/call-site is unaffected. Populated only when
    # `requested_by_type == ActorType.AGENT`, so ApprovalExecutionService's
    # `_reconstruct_context` (approval_execution_service.py) can rebuild
    # the exact ExecutionContext.agent_id/agent_version_id an Agent-
    # initiated call needs to pass registry.py's new agent-governance
    # checks again on resume — the same "reconstruct, never trust a
    # client-supplied value" pattern already used for
    # requested_by_role/requested_by_id. `agent_execution_id` closes the
    # loop the other way: it lets the approval-resume path update the
    # originating AgentExecution's status (WAITING_APPROVAL ->
    # COMPLETED/FAILED) without a second lookup mechanism.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
