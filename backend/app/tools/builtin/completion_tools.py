import uuid
from typing import Any

from pydantic import BaseModel

from app.models.operations import CompletionPacket, CustomerSignoff, Job
from app.models.rbac import Permission
from app.services.completion_service import CloseOutNotReadyError, CompletionService
from app.services.job_state_machine import InvalidJobTransitionError
from app.services.signoff_service import InternalCustomerSignoffProvider
from app.tools.base import ExecutionContext, Tool


def _packet_to_dict(p: CompletionPacket) -> dict[str, Any]:
    return {"id": str(p.id), "job_id": str(p.job_id), "status": p.status, "summary": p.summary}


class JobIdInput(BaseModel):
    job_id: uuid.UUID


class CompletionPacketOutput(BaseModel):
    packet: dict[str, Any]


class GenerateCompletionPacket(Tool):
    name = "operations.generate_completion_packet"
    description = "Assemble a completion packet from real job data (tasks, attachments, materials, QA)."
    input_schema = JobIdInput
    output_schema = CompletionPacketOutput
    required_permission = Permission.CLOSE_JOB

    def __init__(self, completion_service: CompletionService) -> None:
        self._completion_service = completion_service

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> CompletionPacketOutput:
        packet = await self._completion_service.generate_completion_packet(context.tenant_id, input.job_id)
        return CompletionPacketOutput(packet=_packet_to_dict(packet))


class JobOutput(BaseModel):
    job: dict[str, Any]


class CloseJob(Tool):
    name = "operations.close_job"
    description = (
        "Close a COMPLETED job (requires passed QA, completed required tasks, a READY "
        "completion packet) and emit invoice.trigger_requested — no invoice is created (no Finance module yet)."
    )
    input_schema = JobIdInput
    output_schema = JobOutput
    required_permission = Permission.CLOSE_JOB

    def __init__(self, completion_service: CompletionService) -> None:
        self._completion_service = completion_service

    async def execute(self, input: JobIdInput, context: ExecutionContext) -> JobOutput:
        from app.tools.builtin.job_tools import _job_to_dict

        try:
            job = await self._completion_service.close_job(context.tenant_id, input.job_id)
        except (CloseOutNotReadyError, InvalidJobTransitionError) as exc:
            raise ValueError(str(exc)) from exc
        return JobOutput(job=_job_to_dict(job))


class RecordSignoffInput(BaseModel):
    job_id: uuid.UUID
    signed_by: str


class SignoffOutput(BaseModel):
    signoff: dict[str, Any]


class RecordCustomerSignoff(Tool):
    name = "operations.record_customer_signoff"
    description = "Record an internal (not legally binding) customer sign-off for a job."
    input_schema = RecordSignoffInput
    output_schema = SignoffOutput
    required_permission = Permission.CLOSE_JOB

    def __init__(self, signoff_provider: InternalCustomerSignoffProvider) -> None:
        self._signoff_provider = signoff_provider

    async def execute(self, input: RecordSignoffInput, context: ExecutionContext) -> SignoffOutput:
        signoff = await self._signoff_provider.record_signoff(
            context.tenant_id, input.job_id, signed_by=input.signed_by
        )
        return SignoffOutput(signoff=_signoff_to_dict(signoff))


def _signoff_to_dict(s: CustomerSignoff) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "job_id": str(s.job_id),
        "signed_at": s.signed_at.isoformat(),
        "signed_by": s.signed_by,
        "signature_reference": s.signature_reference,
        "provider": s.provider,
    }
