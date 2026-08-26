import uuid
from typing import Any

from pydantic import BaseModel

from app.models.operations import OperationsException
from app.models.rbac import Permission
from app.services.exception_service import ExceptionNotFoundError, ExceptionService
from app.tools.base import ExecutionContext, Tool


def _exception_to_dict(e: OperationsException) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "type": e.type,
        "severity": e.severity,
        "entity_type": e.entity_type,
        "entity_id": str(e.entity_id),
        "description": e.description,
        "recommended_action": e.recommended_action,
        "status": e.status,
        "assigned_to": str(e.assigned_to) if e.assigned_to else None,
        "created_at": e.created_at.isoformat(),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


class CreateExceptionInput(BaseModel):
    type: str
    severity: str
    entity_type: str
    entity_id: uuid.UUID
    description: str
    recommended_action: str | None = None
    assigned_to: uuid.UUID | None = None


class ExceptionOutput(BaseModel):
    exception: dict[str, Any]
    deduplicated: bool = False


class CreateException(Tool):
    name = "operations.create_exception"
    description = "Create an operations exception. Deduplicates on (type, entity_id) while OPEN."
    input_schema = CreateExceptionInput
    output_schema = ExceptionOutput
    required_permission = Permission.MANAGE_EXCEPTIONS

    def __init__(self, exception_service: ExceptionService) -> None:
        self._exception_service = exception_service

    async def execute(self, input: CreateExceptionInput, context: ExecutionContext) -> ExceptionOutput:
        exc, deduped = await self._exception_service.create_exception(
            context.tenant_id,
            type=input.type,
            severity=input.severity,
            entity_type=input.entity_type,
            entity_id=input.entity_id,
            description=input.description,
            recommended_action=input.recommended_action,
            assigned_to=input.assigned_to,
        )
        return ExceptionOutput(exception=_exception_to_dict(exc), deduplicated=deduped)


class ResolveExceptionInput(BaseModel):
    exception_id: uuid.UUID


class ResolveException(Tool):
    name = "operations.resolve_exception"
    description = "Mark an operations exception resolved."
    input_schema = ResolveExceptionInput
    output_schema = ExceptionOutput
    required_permission = Permission.MANAGE_EXCEPTIONS

    def __init__(self, exception_service: ExceptionService) -> None:
        self._exception_service = exception_service

    async def execute(self, input: ResolveExceptionInput, context: ExecutionContext) -> ExceptionOutput:
        try:
            exc = await self._exception_service.resolve_exception(context.tenant_id, input.exception_id)
        except ExceptionNotFoundError as e:
            raise ValueError(str(e)) from e
        return ExceptionOutput(exception=_exception_to_dict(exc))
