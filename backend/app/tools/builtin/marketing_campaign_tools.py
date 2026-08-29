import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.marketing import Campaign
from app.models.rbac import Permission
from app.services.campaign_service import CampaignNotFoundError, CampaignService
from app.tools.base import ExecutionContext, Tool


def _campaign_to_dict(c: Campaign) -> dict[str, Any]:
    return {
        "id": str(c.id), "name": c.name, "channel": c.channel, "objective": c.objective, "status": c.status,
        "monthly_budget": str(c.monthly_budget) if c.monthly_budget is not None else None,
        "total_budget": str(c.total_budget) if c.total_budget is not None else None,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
    }


class CreateCampaignInput(BaseModel):
    name: str
    channel: str
    objective: str = "LEAD_GEN"
    monthly_budget: Decimal | None = None
    total_budget: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None
    external_provider: str | None = None


class CampaignOutput(BaseModel):
    campaign: dict[str, Any]


class CreateCampaign(Tool):
    name = "marketing.create_campaign"
    description = "Create a marketing campaign record."
    input_schema = CreateCampaignInput
    output_schema = CampaignOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    def __init__(self, campaign_service: CampaignService) -> None:
        self._campaign_service = campaign_service

    async def execute(self, input: CreateCampaignInput, context: ExecutionContext) -> CampaignOutput:
        campaign = await self._campaign_service.create_campaign(
            context.tenant_id, name=input.name, channel=input.channel, objective=input.objective,
            monthly_budget=input.monthly_budget, total_budget=input.total_budget, start_date=input.start_date,
            end_date=input.end_date, external_provider=input.external_provider,
        )
        return CampaignOutput(campaign=_campaign_to_dict(campaign))


class SetCampaignStatusInput(BaseModel):
    campaign_id: uuid.UUID
    status: str


class SetCampaignStatus(Tool):
    name = "marketing.set_campaign_status"
    description = "Change a campaign's status (DRAFT/ACTIVE/PAUSED/COMPLETED/ARCHIVED)."
    input_schema = SetCampaignStatusInput
    output_schema = CampaignOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    def __init__(self, campaign_service: CampaignService) -> None:
        self._campaign_service = campaign_service

    async def execute(self, input: SetCampaignStatusInput, context: ExecutionContext) -> CampaignOutput:
        try:
            campaign = await self._campaign_service.set_status(context.tenant_id, input.campaign_id, input.status)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return CampaignOutput(campaign=_campaign_to_dict(campaign))


class AllocationModel(BaseModel):
    campaign_id: uuid.UUID
    amount: Decimal


class RecordSpendInput(BaseModel):
    channel: str
    amount: Decimal
    spend_date: date
    allocations: list[AllocationModel]
    source: str = "manual"
    external_reference: str | None = None


class RecordSpendOutput(BaseModel):
    spend_id: str
    amount: str


class RecordSpend(Tool):
    name = "marketing.record_spend"
    description = "Record a real marketing spend amount and allocate it across one or more campaigns."
    input_schema = RecordSpendInput
    output_schema = RecordSpendOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    def __init__(self, campaign_service: CampaignService) -> None:
        self._campaign_service = campaign_service

    async def execute(self, input: RecordSpendInput, context: ExecutionContext) -> RecordSpendOutput:
        try:
            spend = await self._campaign_service.record_spend(
                context.tenant_id, channel=input.channel, amount=input.amount, spend_date=input.spend_date,
                allocations=[(a.campaign_id, a.amount) for a in input.allocations], source=input.source,
                external_reference=input.external_reference,
            )
        except (CampaignNotFoundError, ValueError) as e:
            raise ValueError(str(e)) from e
        return RecordSpendOutput(spend_id=str(spend.id), amount=str(spend.amount))


class GetBudgetStatusInput(BaseModel):
    campaign_id: uuid.UUID


class GetBudgetStatusOutput(BaseModel):
    budget: str | None
    spend_to_date: str
    remaining: str | None
    utilization_pct: float | None
    alert: str | None


class GetBudgetStatus(Tool):
    name = "marketing.get_budget_status"
    description = "Get a campaign's budget utilization and any alert (80%/90%/100%/overspend)."
    input_schema = GetBudgetStatusInput
    output_schema = GetBudgetStatusOutput
    required_permission = Permission.READ_MARKETING

    def __init__(self, campaign_service: CampaignService) -> None:
        self._campaign_service = campaign_service

    async def execute(self, input: GetBudgetStatusInput, context: ExecutionContext) -> GetBudgetStatusOutput:
        try:
            status = await self._campaign_service.budget_status(context.tenant_id, input.campaign_id)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return GetBudgetStatusOutput(
            budget=str(status.budget) if status.budget is not None else None,
            spend_to_date=str(status.spend_to_date),
            remaining=str(status.remaining) if status.remaining is not None else None,
            utilization_pct=status.utilization_pct, alert=status.alert,
        )
