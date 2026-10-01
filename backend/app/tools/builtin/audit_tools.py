"""audit.record_action: a general-purpose way for any tool or workflow to
write a manual AuditLog row outside the automatic per-tool-call audit
ToolRegistry.execute() already writes for every call — e.g. a note about a
manual/offline decision that has no corresponding tool call of its own.
Open to any authenticated actor, same as notifications.create_notification;
the row itself, not a permission check, is the record of who did what.

Phase 9: audit.list_ai_activity, below, is what the /ai-activity page reads
through. It is a filtered view over this SAME AuditLog table — no second
audit store. "AI activity" here means: every AI-actor tool call
(actor_type=AI, mostly the insights.* snapshot calls and
insights.generate_morning_brief), plus the full approval lifecycle those
calls can lead to (approvals.* actions and approval.execution.* outcomes) —
so a human reviewing this page can trace an AI recommendation all the way
through approval and execution using rows that already exist for other
reasons, not a bespoke log."""

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


class RecordActionInput(BaseModel):
    action: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    result: str = "success"
    note: str | None = None


class RecordActionOutput(BaseModel):
    audit_log_id: str


class RecordAction(Tool):
    name = "audit.record_action"
    description = "Record a manual audit log entry for an action with no corresponding tool call of its own."
    input_schema = RecordActionInput
    output_schema = RecordActionOutput

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: RecordActionInput, context: ExecutionContext) -> RecordActionOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            entry = AuditLog(
                tenant_id=context.tenant_id,
                actor_type=context.actor_type,
                actor_id=context.actor_id,
                action=input.action,
                tool=None,
                entity_type=input.entity_type,
                entity_id=input.entity_id,
                input_summary={"note": input.note} if input.note else None,
                result=input.result,
                correlation_id=context.correlation_id,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return RecordActionOutput(audit_log_id=str(entry.id))


class ListAIActivityInput(BaseModel):
    limit: int = 100


class AIActivityRow(BaseModel):
    id: str
    actor_type: str
    actor_id: str | None
    action: str
    tool: str | None
    entity_type: str | None
    entity_id: str | None
    result: str
    approval_id: str | None
    created_at: str


class ListAIActivityOutput(BaseModel):
    rows: list[AIActivityRow]


_AI_RELATED_TOOL_PREFIXES = ("insights.", "approvals.")


class ListAIActivity(Tool):
    name = "audit.list_ai_activity"
    description = "List AuditLog rows for AI-driven activity and the approval lifecycle it led to."
    input_schema = ListAIActivityInput
    output_schema = ListAIActivityOutput
    required_permission = Permission.READ_AI_ACTIVITY

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: ListAIActivityInput, context: ExecutionContext) -> ListAIActivityOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            conditions = [AuditLog.actor_type == ActorType.AI] + [
                AuditLog.tool.like(f"{prefix}%") for prefix in _AI_RELATED_TOOL_PREFIXES
            ] + [AuditLog.action.like("approval.%")]
            rows = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.tenant_id == context.tenant_id, or_(*conditions))
                    .order_by(AuditLog.created_at.desc())
                    .limit(input.limit)
                )
            ).scalars().all()
            return ListAIActivityOutput(
                rows=[
                    AIActivityRow(
                        id=str(r.id),
                        actor_type=r.actor_type,
                        actor_id=str(r.actor_id) if r.actor_id else None,
                        action=r.action,
                        tool=r.tool,
                        entity_type=r.entity_type,
                        entity_id=str(r.entity_id) if r.entity_id else None,
                        result=r.result,
                        approval_id=str(r.approval_id) if r.approval_id else None,
                        created_at=r.created_at.isoformat() if isinstance(r.created_at, datetime) else str(r.created_at),
                    )
                    for r in rows
                ]
            )
