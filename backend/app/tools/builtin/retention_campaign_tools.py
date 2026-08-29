import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.retention_campaign_service import CampaignNotFoundError, RetentionCampaignService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class CreateCampaignInput(BaseModel):
    name: str
    type: str


class CampaignOutput(BaseModel):
    campaign_id: str
    status: str


class CreateRetentionCampaign(Tool):
    name = "retention.create_campaign"
    description = "Create a retention campaign (POST_JOB_FOLLOWUP/SERVICE_REMINDER/WIN_BACK/REVIEW_REQUEST/REFERRAL_INVITE/VIP_CUSTOMER/SERVICE_RECOVERY)."
    input_schema = CreateCampaignInput
    output_schema = CampaignOutput
    required_permission = Permission.MANAGE_RETENTION_CAMPAIGNS

    def __init__(self, retention_campaign_service: RetentionCampaignService) -> None:
        self._retention_campaign_service = retention_campaign_service

    async def execute(self, input: CreateCampaignInput, context: ExecutionContext) -> CampaignOutput:
        campaign = await self._retention_campaign_service.create_campaign(context.tenant_id, name=input.name, type=input.type)
        return CampaignOutput(campaign_id=str(campaign.id), status=campaign.status)


class SetCampaignStatusInput(BaseModel):
    campaign_id: uuid.UUID
    status: str


class SetRetentionCampaignStatus(Tool):
    name = "retention.set_campaign_status"
    description = "Change a retention campaign's status."
    input_schema = SetCampaignStatusInput
    output_schema = CampaignOutput
    required_permission = Permission.MANAGE_RETENTION_CAMPAIGNS

    def __init__(self, retention_campaign_service: RetentionCampaignService) -> None:
        self._retention_campaign_service = retention_campaign_service

    async def execute(self, input: SetCampaignStatusInput, context: ExecutionContext) -> CampaignOutput:
        try:
            campaign = await self._retention_campaign_service.set_status(context.tenant_id, input.campaign_id, input.status)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return CampaignOutput(campaign_id=str(campaign.id), status=campaign.status)


class EnrollCustomerInput(BaseModel):
    campaign_id: uuid.UUID
    customer_id: uuid.UUID


class EnrollmentOutput(BaseModel):
    enrollment_id: str
    status: str


class EnrollCustomerInRetentionCampaign(Tool):
    """`AUTO` at the policy layer — enrollment only schedules a future
    `RetentionActivity`, and execution only ever reaches the internal test
    communication provider until a real external one is connected. Same
    reasoning as Phase 6's outbound/nurture enrollment — see
    app/tools/policy.py."""

    name = "retention.enroll_customer_in_campaign"
    description = "Enroll a customer in a retention campaign."
    input_schema = EnrollCustomerInput
    output_schema = EnrollmentOutput
    required_permission = Permission.SEND_RETENTION_COMMUNICATION

    def __init__(self, retention_campaign_service: RetentionCampaignService) -> None:
        self._retention_campaign_service = retention_campaign_service

    async def execute(self, input: EnrollCustomerInput, context: ExecutionContext) -> EnrollmentOutput:
        try:
            enrollment = await self._retention_campaign_service.enroll_customer(context.tenant_id, input.campaign_id, input.customer_id)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return EnrollmentOutput(enrollment_id=str(enrollment.id), status=enrollment.status)


class ExecuteDueOutput(BaseModel):
    executed_activity_ids: list[str]


class ExecuteDueRetentionActivities(Tool):
    name = "retention.execute_due_activities"
    description = "Execute all PENDING retention activities whose scheduled_for time has passed."
    input_schema = EmptyInput
    output_schema = ExecuteDueOutput
    required_permission = Permission.SEND_RETENTION_COMMUNICATION

    def __init__(self, retention_campaign_service: RetentionCampaignService) -> None:
        self._retention_campaign_service = retention_campaign_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> ExecuteDueOutput:
        ids = await self._retention_campaign_service.execute_due_activities(context.tenant_id)
        return ExecuteDueOutput(executed_activity_ids=[str(i) for i in ids])
