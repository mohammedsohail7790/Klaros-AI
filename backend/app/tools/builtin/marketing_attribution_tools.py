import uuid
from typing import Any

from pydantic import BaseModel

from app.models.marketing import LeadAttribution
from app.models.rbac import Permission
from app.services.attribution_service import AttributionService, CampaignNotFoundError, LeadNotFoundError
from app.services.campaign_service import CampaignService
from app.tools.base import ExecutionContext, Tool


def _attribution_to_dict(a: LeadAttribution) -> dict[str, Any]:
    return {
        "id": str(a.id), "lead_id": str(a.lead_id), "campaign_id": str(a.campaign_id) if a.campaign_id else None,
        "source": a.source, "medium": a.medium, "attribution_model": a.attribution_model,
    }


class AttributeLeadInput(BaseModel):
    lead_id: uuid.UUID
    campaign_id: uuid.UUID | None = None
    source: str | None = None
    medium: str | None = None
    landing_page: str | None = None
    referral_source: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_term: str | None = None
    utm_content: str | None = None
    click_id: str | None = None
    attribution_model: str = "SOURCE_ONLY"


class AttributionOutput(BaseModel):
    attribution: dict[str, Any]


class AttributeLead(Tool):
    name = "marketing.attribute_lead"
    description = "Record a lead's marketing attribution context (source/campaign/UTM)."
    input_schema = AttributeLeadInput
    output_schema = AttributionOutput
    required_permission = Permission.READ_MARKETING

    def __init__(self, attribution_service: AttributionService) -> None:
        self._attribution_service = attribution_service

    async def execute(self, input: AttributeLeadInput, context: ExecutionContext) -> AttributionOutput:
        try:
            attribution = await self._attribution_service.attribute_lead(
                context.tenant_id, input.lead_id, campaign_id=input.campaign_id, source=input.source,
                medium=input.medium, landing_page=input.landing_page, referral_source=input.referral_source,
                utm_source=input.utm_source, utm_medium=input.utm_medium, utm_campaign=input.utm_campaign,
                utm_term=input.utm_term, utm_content=input.utm_content, click_id=input.click_id,
                attribution_model=input.attribution_model,
            )
        except (LeadNotFoundError, CampaignNotFoundError) as e:
            raise ValueError(str(e)) from e
        return AttributionOutput(attribution=_attribution_to_dict(attribution))


class GetCampaignPerformanceInput(BaseModel):
    campaign_id: uuid.UUID


class GetCampaignPerformanceOutput(BaseModel):
    campaign_id: str
    spend: str
    leads: int
    qualified_leads: int
    booked: int
    jobs_created: int
    jobs_closed: int
    invoiced_count: int
    revenue: str
    collected_revenue: str
    cac: str | None
    cac_note: str | None
    cost_per_qualified_lead: str | None
    revenue_per_lead: str | None
    roas: str | None
    roas_note: str | None


class GetCampaignPerformance(Tool):
    name = "marketing.get_campaign_performance"
    description = "Compute a campaign's real spend/leads/revenue/CAC/ROAS from underlying rows only."
    input_schema = GetCampaignPerformanceInput
    output_schema = GetCampaignPerformanceOutput
    required_permission = Permission.VIEW_MARKETING_ANALYTICS

    def __init__(self, attribution_service: AttributionService) -> None:
        self._attribution_service = attribution_service

    async def execute(self, input: GetCampaignPerformanceInput, context: ExecutionContext) -> GetCampaignPerformanceOutput:
        try:
            perf = await self._attribution_service.campaign_performance(context.tenant_id, input.campaign_id)
        except CampaignNotFoundError as e:
            raise ValueError(str(e)) from e
        return GetCampaignPerformanceOutput(
            campaign_id=str(perf.campaign_id), spend=str(perf.spend), leads=perf.leads,
            qualified_leads=perf.qualified_leads, booked=perf.booked, jobs_created=perf.jobs_created,
            jobs_closed=perf.jobs_closed, invoiced_count=perf.invoiced_count, revenue=str(perf.revenue),
            collected_revenue=str(perf.collected_revenue), cac=str(perf.cac) if perf.cac is not None else None,
            cac_note=perf.cac_note,
            cost_per_qualified_lead=str(perf.cost_per_qualified_lead) if perf.cost_per_qualified_lead is not None else None,
            revenue_per_lead=str(perf.revenue_per_lead) if perf.revenue_per_lead is not None else None,
            roas=str(perf.roas) if perf.roas is not None else None, roas_note=perf.roas_note,
        )


class EmptyInput(BaseModel):
    pass


class DetectPerformanceExceptionsOutput(BaseModel):
    flagged_campaign_ids: list[str]


class DetectPerformanceExceptions(Tool):
    name = "marketing.detect_performance_exceptions"
    description = "Scan all campaigns for HIGH_CAC / LOW_CONVERSION and open exceptions deterministically."
    input_schema = EmptyInput
    output_schema = DetectPerformanceExceptionsOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    def __init__(self, campaign_service: CampaignService, attribution_service: AttributionService) -> None:
        self._campaign_service = campaign_service
        self._attribution_service = attribution_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectPerformanceExceptionsOutput:
        flagged = await self._campaign_service.detect_performance_exceptions(context.tenant_id, self._attribution_service)
        return DetectPerformanceExceptionsOutput(flagged_campaign_ids=[str(c) for c in flagged])
