import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit_log import AuditLog
from app.tools.base import ExecutionContext, Tool
from app.tools.redact import redact_input


class RecordActionInput(BaseModel):
    action: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    input_summary: dict[str, Any] = {}
    result: str = "success"


class RecordActionOutput(BaseModel):
    audit_log_id: str


class RecordAction(Tool):
    """Lets a workflow/activity record an audit entry for an action it took
    outside the tool registry (e.g. a Temporal activity that isn't itself
    wrapped as a Tool yet). Prefer a real typed tool where one exists.
    """

    name = "audit.record_action"
    description = "Record an audit log entry for an action taken outside the tool registry."
    input_schema = RecordActionInput
    output_schema = RecordActionOutput

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: RecordActionInput, context: ExecutionContext) -> RecordActionOutput:
        async with self._session_factory() as session:
            log = AuditLog(
                tenant_id=context.tenant_id,
                actor_type=context.actor_type,
                actor_id=context.actor_id,
                action=input.action,
                entity_type=input.entity_type,
                entity_id=input.entity_id,
                input_summary=redact_input(input.input_summary),
                result=input.result,
                correlation_id=context.correlation_id,
            )
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return RecordActionOutput(audit_log_id=str(log.id))
