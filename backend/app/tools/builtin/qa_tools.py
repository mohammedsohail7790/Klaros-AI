import uuid
from typing import Any

from pydantic import BaseModel

from app.models.operations import JobQA
from app.models.rbac import Permission
from app.services.qa_service import QANotReadyError, QAService, QAValidationError
from app.tools.base import ExecutionContext, Tool


def _qa_to_dict(qa: JobQA) -> dict[str, Any]:
    return {
        "id": str(qa.id),
        "job_id": str(qa.job_id),
        "status": qa.status,
        "checks": qa.checks,
        "failure_reason": qa.failure_reason,
    }


class JobIdInput(BaseModel):
    job_id: uuid.UUID


class QAOutput(BaseModel):
    qa: dict[str, Any]


class StartQA(Tool):
    name = "operations.start_qa"
    description = "Begin QA review for a job (must be QA_PENDING)."
    input_schema = JobIdInput
    output_schema = QAOutput
    required_permission = Permission.MANAGE_QA

    def __init__(self, qa_service: QAService) -> None:
        self._qa_service = qa_service

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> QAOutput:
        try:
            qa = await self._qa_service.start_qa(context.tenant_id, input.job_id)
        except QANotReadyError as exc:
            raise ValueError(str(exc)) from exc
        return QAOutput(qa=_qa_to_dict(qa))


class CompleteQA(Tool):
    name = "operations.complete_qa"
    description = "Pass QA — re-checks required tasks/documentation itself; fails loudly if incomplete."
    input_schema = JobIdInput
    output_schema = QAOutput
    required_permission = Permission.MANAGE_QA

    def __init__(self, qa_service: QAService) -> None:
        self._qa_service = qa_service

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> QAOutput:
        try:
            qa = await self._qa_service.complete_qa(context.tenant_id, input.job_id, context.actor_id)
        except (QANotReadyError, QAValidationError) as exc:
            raise ValueError(str(exc)) from exc
        return QAOutput(qa=_qa_to_dict(qa))


class FailQAInput(BaseModel):
    job_id: uuid.UUID
    reason: str


class FailQA(Tool):
    name = "operations.fail_qa"
    description = "Fail QA with a reason. Job remains QA_PENDING; a QA_FAILURE exception is created."
    input_schema = FailQAInput
    output_schema = QAOutput
    required_permission = Permission.MANAGE_QA

    def __init__(self, qa_service: QAService) -> None:
        self._qa_service = qa_service

    async def execute(self, input: FailQAInput, context: ExecutionContext) -> QAOutput:
        qa = await self._qa_service.fail_qa(context.tenant_id, input.job_id, input.reason)
        return QAOutput(qa=_qa_to_dict(qa))
