import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.reactivation_service import CampaignNotFoundError, ReactivationService
from app.tools.base import ExecutionContext, Tool


class CreateReactivationCampaignInput(BaseModel):
    name: str
    target_criteria: str | None = None


class CampaignOutput(BaseModel):
    campaign_id: str


class CreateReactivationCampaign(Tool):
    name = "marketing.create_reactivation_campaign"
    description = "Create a reactivation campaign."
    input_schema = CreateReactivationCampaignInput
    output_schema = CampaignOutput
    required_permission = Permission.MANAGE_REACTIVATION

    def __init__(self, reactivation_service: ReactivationService) -> None:
        self._reactivation_service = reactivation_service

    async def execute(self, input: CreateReactivationCampaignInput, context: ExecutionContext) -> CampaignOutput:
        row = await self._reactivation_service.create_campaign(
            context.tenant_id, name=input.name, target_criteria=input.target_criteria
        )
        return CampaignOutput(campaign_id=str(row.id))


class IdentifyCandidatesInput(BaseModel):
    campaign_id: uuid.UUID


class CandidatesOutput(BaseModel):
    candidate_ids: list[str]


class IdentifyInactiveCustomers(Tool):
    name = "marketing.identify_inactive_customers"
    description = "Deterministically find customers with no job in 180+ days and create reactivation candidates."
    input_schema = IdentifyCandidatesInput
    output_schema = CandidatesOutput
    required_permission = Permission.MANAGE_REACTIVATION

    def __init__(self, reactivation_service: ReactivationService) -> None:
        self._reactivation_service = reactivation_service

    async def execute(self, input: IdentifyCandidatesInput, context: ExecutionContext) -> CandidatesOutput:
        try:
            candidates = await self._reactivation_service.identify_inactive_customers(context.tenant_id, input.campaign_id)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return CandidatesOutput(candidate_ids=[str(c.id) for c in candidates])


class IdentifyUnbookedQualifiedLeads(Tool):
    name = "marketing.identify_unbooked_qualified_leads"
    description = "Deterministically find previously-qualified-but-never-booked leads and create reactivation candidates."
    input_schema = IdentifyCandidatesInput
    output_schema = CandidatesOutput
    required_permission = Permission.MANAGE_REACTIVATION

    def __init__(self, reactivation_service: ReactivationService) -> None:
        self._reactivation_service = reactivation_service

    async def execute(self, input: IdentifyCandidatesInput, context: ExecutionContext) -> CandidatesOutput:
        try:
            candidates = await self._reactivation_service.identify_unbooked_qualified_leads(context.tenant_id, input.campaign_id)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return CandidatesOutput(candidate_ids=[str(c.id) for c in candidates])
