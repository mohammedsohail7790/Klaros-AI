"""The generic Automation Engine: EVENT_TRIGGER -> CONDITION -> ACTION,
tenant-scoped, versioned, durable, and governed through the exact same
ToolRegistry boundary every other mutation in this codebase uses — see
app/services/automation_service.py and app/services/automation_condition.py.

Deliberately reuses, rather than duplicates:
  - EventBus (app/events/bus.py) for the trigger transport
  - AIExecutionService -> ToolRegistry for every action (same boundary as
    Morning Brief's AI mode and the Voice Receptionist's tool allowlist)
  - Temporal (app/workflows/) for durable waits, via a dedicated
    AutomationExecutionWorkflow — never asyncio.sleep in a worker process

Versioning: editing an Automation creates a NEW AutomationVersion; an
in-flight AutomationExecution always keeps executing against the
AutomationVersion it started with (FK, immutable once created), never the
tenant's newly-edited definition — exactly the "must continue against its
execution/version definition even if the owner edits it afterward"
requirement.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class TriggerType(StrEnum):
    EVENT = "EVENT"
    SCHEDULE = "SCHEDULE"
    MANUAL = "MANUAL"


class AutomationStatus(StrEnum):
    DRAFT = "DRAFT"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Automation(TenantScopedMixin, Base):
    __tablename__ = "automations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AutomationStatus.DRAFT)
    # The currently-published version new executions start from. NULL
    # until the automation has been published at least once (a DRAFT
    # automation with no published version can never trigger).
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class AutomationVersion(TenantScopedMixin, Base):
    """Immutable once created — publishing a new edit always inserts a new
    row, never mutates an existing one, so an AutomationExecution's FK to
    a specific version can never change out from under it."""

    __tablename__ = "automation_versions"
    __table_args__ = (UniqueConstraint("automation_id", "version_number", name="uq_automation_version_number"),)

    automation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # EVENT: {"event_type": "lead.created"}. SCHEDULE: {"cron": "..."} (not
    # yet wired to a real scheduler — see AutomationService docstring).
    # MANUAL: {}.
    trigger_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # A condition NODE (see automation_condition.py) or None (always true).
    condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Ordered list of {"action": "<tool-name-or-wait>", "params": {...}}.
    steps: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class AutomationExecution(TenantScopedMixin, Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        # Real idempotency: the same source event can never start a second
        # execution of the same automation version, even under concurrent
        # delivery/retry.
        UniqueConstraint(
            "automation_version_id", "source_event_id", name="uq_automation_execution_version_event"
        ),
    )

    automation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    automation_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_versions.id"), nullable=False, index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The EventBus Event.id that triggered this execution, when
    # trigger_type == EVENT — NULL for MANUAL/SCHEDULE. Part of the real
    # idempotency guarantee above.
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ExecutionStatus.PENDING)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The bounded, serializable, tenant-scoped variables available to
    # condition/action evaluation — never raw ORM objects, never secrets.
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class AutomationExecutionStep(TenantScopedMixin, Base):
    __tablename__ = "automation_execution_steps"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StepStatus.PENDING)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
