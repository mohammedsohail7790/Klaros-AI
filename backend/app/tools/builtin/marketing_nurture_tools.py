import uuid

from pydantic import BaseModel

from app.models.marketing import NurtureTriggerType
from app.models.rbac import Permission
from app.services.nurture_service import NurtureService, SequenceNotFoundError
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class CreateNurtureSequenceInput(BaseModel):
    name: str
    trigger_type: str = "CUSTOM"


class SequenceOutput(BaseModel):
    sequence_id: str


class CreateNurtureSequence(Tool):
    name = "marketing.create_nurture_sequence"
    description = "Create a nurture sequence for stale/unbooked leads."
    input_schema = CreateNurtureSequenceInput
    output_schema = SequenceOutput
    required_permission = Permission.MANAGE_NURTURE

    def __init__(self, nurture_service: NurtureService) -> None:
        self._nurture_service = nurture_service

    async def execute(self, input: CreateNurtureSequenceInput, context: ExecutionContext) -> SequenceOutput:
        try:
            NurtureTriggerType(input.trigger_type)
        except ValueError:
            raise ValueError(f"Invalid nurture trigger type: {input.trigger_type}") from None
        row = await self._nurture_service.create_sequence(context.tenant_id, name=input.name, trigger_type=input.trigger_type)
        return SequenceOutput(sequence_id=str(row.id))


class FindCandidatesOutput(BaseModel):
    lead_ids: list[str]


class FindStaleLeadCandidates(Tool):
    name = "marketing.find_stale_lead_candidates"
    description = "Find leads with no status change in 30+ days (deterministic, no LLM)."
    input_schema = EmptyInput
    output_schema = FindCandidatesOutput
    required_permission = Permission.MANAGE_NURTURE

    def __init__(self, nurture_service: NurtureService) -> None:
        self._nurture_service = nurture_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> FindCandidatesOutput:
        leads = await self._nurture_service.find_stale_lead_candidates(context.tenant_id)
        return FindCandidatesOutput(lead_ids=[str(l.id) for l in leads])


class EnrollLeadInput(BaseModel):
    sequence_id: uuid.UUID
    lead_id: uuid.UUID


class EnrollmentOutput(BaseModel):
    enrollment_id: str
    status: str


class EnrollLeadInNurture(Tool):
    """`AUTO` at the policy layer — enrolling only schedules future
    activities, and execution only ever reaches the internal test
    communication provider until a real external one is connected."""

    name = "marketing.enroll_lead_in_nurture"
    description = "Enroll a lead into a nurture sequence."
    input_schema = EnrollLeadInput
    output_schema = EnrollmentOutput
    required_permission = Permission.MANAGE_NURTURE

    def __init__(self, nurture_service: NurtureService) -> None:
        self._nurture_service = nurture_service

    async def execute(self, input: EnrollLeadInput, context: ExecutionContext) -> EnrollmentOutput:
        try:
            enrollment = await self._nurture_service.enroll_lead(context.tenant_id, input.sequence_id, input.lead_id)
        except SequenceNotFoundError as e:
            raise ValueError(str(e)) from e
        return EnrollmentOutput(enrollment_id=str(enrollment.id), status=enrollment.status)


class ExecuteDueOutput(BaseModel):
    executed_activity_ids: list[str]


class ExecuteDueNurtureActivities(Tool):
    name = "marketing.execute_due_nurture_activities"
    description = "Execute all PENDING nurture activities whose scheduled_for time has passed."
    input_schema = EmptyInput
    output_schema = ExecuteDueOutput
    required_permission = Permission.MANAGE_NURTURE

    def __init__(self, nurture_service: NurtureService) -> None:
        self._nurture_service = nurture_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> ExecuteDueOutput:
        ids = await self._nurture_service.execute_due_activities(context.tenant_id)
        return ExecuteDueOutput(executed_activity_ids=[str(i) for i in ids])
