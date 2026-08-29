import uuid
from typing import Any

from pydantic import BaseModel

from app.models.marketing import OutboundContact, OutboundEnrollment, OutboundList, OutboundSequence
from app.models.rbac import Permission
from app.services.outbound_service import (
    ContactNotFoundError,
    DuplicateContactError,
    ListNotFoundError,
    OutboundService,
    SequenceNotFoundError,
)
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class CreateListInput(BaseModel):
    name: str
    description: str | None = None


class ListOutput(BaseModel):
    list_id: str
    name: str


class CreateOutboundList(Tool):
    name = "marketing.create_outbound_list"
    description = "Create an outbound contact list."
    input_schema = CreateListInput
    output_schema = ListOutput
    required_permission = Permission.MANAGE_OUTBOUND

    def __init__(self, outbound_service: OutboundService) -> None:
        self._outbound_service = outbound_service

    async def execute(self, input: CreateListInput, context: ExecutionContext) -> ListOutput:
        row = await self._outbound_service.create_list(context.tenant_id, name=input.name, description=input.description)
        return ListOutput(list_id=str(row.id), name=row.name)


class AddContactInput(BaseModel):
    list_id: uuid.UUID
    company: str | None = None
    contact_name: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    industry: str | None = None
    location: str | None = None
    service_relevance: str | None = None
    source: str = "MANUAL"


class ContactOutput(BaseModel):
    contact_id: str


class AddOutboundContact(Tool):
    name = "marketing.add_outbound_contact"
    description = "Add a contact to an outbound list. Rejects duplicates by normalized email/phone within the tenant."
    input_schema = AddContactInput
    output_schema = ContactOutput
    required_permission = Permission.MANAGE_OUTBOUND

    def __init__(self, outbound_service: OutboundService) -> None:
        self._outbound_service = outbound_service

    async def execute(self, input: AddContactInput, context: ExecutionContext) -> ContactOutput:
        try:
            contact = await self._outbound_service.add_contact(
                context.tenant_id, input.list_id, company=input.company, contact_name=input.contact_name,
                email=input.email, phone=input.phone, role=input.role, website=input.website,
                industry=input.industry, location=input.location, service_relevance=input.service_relevance,
                source=input.source,
            )
        except (ListNotFoundError, DuplicateContactError) as e:
            raise ValueError(str(e)) from e
        return ContactOutput(contact_id=str(contact.id))


class CreateSequenceInput(BaseModel):
    name: str
    description: str | None = None


class SequenceOutput(BaseModel):
    sequence_id: str


class CreateOutboundSequence(Tool):
    name = "marketing.create_outbound_sequence"
    description = "Create an outbound sequence (day-offset steps added separately)."
    input_schema = CreateSequenceInput
    output_schema = SequenceOutput
    required_permission = Permission.MANAGE_OUTBOUND

    def __init__(self, outbound_service: OutboundService) -> None:
        self._outbound_service = outbound_service

    async def execute(self, input: CreateSequenceInput, context: ExecutionContext) -> SequenceOutput:
        row = await self._outbound_service.create_sequence(context.tenant_id, name=input.name, description=input.description)
        return SequenceOutput(sequence_id=str(row.id))


class AddStepInput(BaseModel):
    sequence_id: uuid.UUID
    day_offset: int
    channel: str = "EMAIL"
    subject: str | None = None
    body: str | None = None
    sort_order: int = 0


class StepOutput(BaseModel):
    step_id: str


class AddOutboundStep(Tool):
    name = "marketing.add_outbound_step"
    description = "Add a day-offset step (e.g. Day 0 Email, Day 2 Email, Day 5 SMS) to a sequence."
    input_schema = AddStepInput
    output_schema = StepOutput
    required_permission = Permission.MANAGE_OUTBOUND

    def __init__(self, outbound_service: OutboundService) -> None:
        self._outbound_service = outbound_service

    async def execute(self, input: AddStepInput, context: ExecutionContext) -> StepOutput:
        try:
            row = await self._outbound_service.add_step(
                context.tenant_id, input.sequence_id, day_offset=input.day_offset, channel=input.channel,
                subject=input.subject, body=input.body, sort_order=input.sort_order,
            )
        except SequenceNotFoundError as e:
            raise ValueError(str(e)) from e
        return StepOutput(step_id=str(row.id))


class EnrollContactInput(BaseModel):
    sequence_id: uuid.UUID
    contact_id: uuid.UUID


class EnrollmentOutput(BaseModel):
    enrollment_id: str
    status: str


class EnrollOutboundContact(Tool):
    """Enrollment only schedules future `OutboundActivity` rows — it never
    sends anything itself. `AUTO` at the policy layer since execution only
    ever reaches the internal test communication provider until a real
    external one is connected — see app/tools/policy.py."""

    name = "marketing.enroll_outbound_contact"
    description = "Enroll a contact into an outbound sequence."
    input_schema = EnrollContactInput
    output_schema = EnrollmentOutput
    required_permission = Permission.MANAGE_OUTBOUND

    def __init__(self, outbound_service: OutboundService) -> None:
        self._outbound_service = outbound_service

    async def execute(self, input: EnrollContactInput, context: ExecutionContext) -> EnrollmentOutput:
        try:
            enrollment = await self._outbound_service.enroll_contact(context.tenant_id, input.sequence_id, input.contact_id)
        except (SequenceNotFoundError, ContactNotFoundError) as e:
            raise ValueError(str(e)) from e
        return EnrollmentOutput(enrollment_id=str(enrollment.id), status=enrollment.status)


class ExecuteDueActivitiesOutput(BaseModel):
    executed_activity_ids: list[str]


class ExecuteDueOutboundActivities(Tool):
    name = "marketing.execute_due_outbound_activities"
    description = "Execute all PENDING outbound activities whose scheduled_for time has passed."
    input_schema = EmptyInput
    output_schema = ExecuteDueActivitiesOutput
    required_permission = Permission.MANAGE_OUTBOUND

    def __init__(self, outbound_service: OutboundService) -> None:
        self._outbound_service = outbound_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> ExecuteDueActivitiesOutput:
        ids = await self._outbound_service.execute_due_activities(context.tenant_id)
        return ExecuteDueActivitiesOutput(executed_activity_ids=[str(i) for i in ids])
