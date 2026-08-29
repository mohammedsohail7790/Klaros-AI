import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.retention_service import RetentionService
from app.tools.base import ExecutionContext, Tool


class UpdateOpportunityInput(BaseModel):
    opportunity_id: uuid.UUID
    status: str


class OpportunityOutput(BaseModel):
    opportunity_id: str
    status: str


class UpdateOpportunityStatus(Tool):
    name = "retention.update_opportunity_status"
    description = "Update a retention opportunity's status (CONTACTED/CONVERTED/DISMISSED/EXPIRED)."
    input_schema = UpdateOpportunityInput
    output_schema = OpportunityOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: UpdateOpportunityInput, context: ExecutionContext) -> OpportunityOutput:
        try:
            opp = await self._retention_service.update_opportunity_status(context.tenant_id, input.opportunity_id, input.status)
        except ValueError as e:
            raise ValueError(str(e)) from e
        return OpportunityOutput(opportunity_id=str(opp.id), status=opp.status)
