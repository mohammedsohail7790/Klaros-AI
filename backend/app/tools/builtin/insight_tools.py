"""Phase 8B: the only surface the Morning Brief (or any future AI feature)
is allowed to use to read business data — real, read-only, permission-gated
Tools, called through ToolRegistry exactly like every mutating tool. See
app/services/morning_brief_service.py for the caller.
"""

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.insight_service import InsightService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class FinanceSnapshotOutput(BaseModel):
    total_ar: str
    overdue_invoice_count: int
    pending_approval_count: int
    collected_last_24h: str
    payments_last_24h_count: int


class GetFinanceSnapshot(Tool):
    name = "insights.get_finance_snapshot"
    description = "Real, read-only finance numbers for the Morning Brief: AR, overdue invoices, cash collected."
    input_schema = EmptyInput
    output_schema = FinanceSnapshotOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> FinanceSnapshotOutput:
        s = await self._insight_service.finance_snapshot(context.tenant_id)
        return FinanceSnapshotOutput(
            total_ar=str(s.total_ar),
            overdue_invoice_count=s.overdue_invoice_count,
            pending_approval_count=s.pending_approval_count,
            collected_last_24h=str(s.collected_last_24h),
            payments_last_24h_count=s.payments_last_24h_count,
        )


class OperationsSnapshotOutput(BaseModel):
    jobs_closed_last_24h: int
    blocked_jobs_count: int
    unassigned_scheduled_jobs_count: int
    open_exceptions_count: int


class GetOperationsSnapshot(Tool):
    name = "insights.get_operations_snapshot"
    description = "Real, read-only operations numbers: jobs closed, blocked jobs, unassigned jobs, open exceptions."
    input_schema = EmptyInput
    output_schema = OperationsSnapshotOutput
    required_permission = Permission.READ_JOBS

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> OperationsSnapshotOutput:
        s = await self._insight_service.operations_snapshot(context.tenant_id)
        return OperationsSnapshotOutput(
            jobs_closed_last_24h=s.jobs_closed_last_24h,
            blocked_jobs_count=s.blocked_jobs_count,
            unassigned_scheduled_jobs_count=s.unassigned_scheduled_jobs_count,
            open_exceptions_count=s.open_exceptions_count,
        )


class SalesSnapshotOutput(BaseModel):
    new_leads_today: int
    qualified_leads_today: int
    appointments_today: int
    qualified_leads_awaiting_appointment: list[dict]


class GetSalesSnapshot(Tool):
    name = "insights.get_sales_snapshot"
    description = "Real, read-only sales numbers: new/qualified leads today, appointments today, stalled qualified leads."
    input_schema = EmptyInput
    output_schema = SalesSnapshotOutput
    required_permission = Permission.READ_LEADS

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> SalesSnapshotOutput:
        s = await self._insight_service.sales_snapshot(context.tenant_id)
        return SalesSnapshotOutput(
            new_leads_today=s.new_leads_today,
            qualified_leads_today=s.qualified_leads_today,
            appointments_today=s.appointments_today,
            qualified_leads_awaiting_appointment=s.qualified_leads_awaiting_appointment,
        )


class CommercialPipelineSnapshotOutput(BaseModel):
    contracts_awaiting_signature: list[dict]
    deposits_awaiting_payment: list[dict]
    stale_quotes_awaiting_response: list[dict]


class GetCommercialPipelineSnapshot(Tool):
    name = "insights.get_commercial_pipeline_snapshot"
    description = "Real, read-only commercial pipeline: contracts awaiting signature, deposits awaiting payment, stale quotes."
    input_schema = EmptyInput
    output_schema = CommercialPipelineSnapshotOutput
    required_permission = Permission.SEND_QUOTE

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> CommercialPipelineSnapshotOutput:
        s = await self._insight_service.commercial_pipeline_snapshot(context.tenant_id)
        return CommercialPipelineSnapshotOutput(
            contracts_awaiting_signature=s.contracts_awaiting_signature,
            deposits_awaiting_payment=s.deposits_awaiting_payment,
            stale_quotes_awaiting_response=s.stale_quotes_awaiting_response,
        )


class MarketingSnapshotOutput(BaseModel):
    active_campaign_count: int
    spend_last_30d: str
    leads_last_24h: int


class GetMarketingSnapshot(Tool):
    name = "insights.get_marketing_snapshot"
    description = "Real, read-only marketing numbers: active campaigns, 30-day spend, leads in the last 24h."
    input_schema = EmptyInput
    output_schema = MarketingSnapshotOutput
    required_permission = Permission.READ_MARKETING

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> MarketingSnapshotOutput:
        s = await self._insight_service.marketing_snapshot(context.tenant_id)
        return MarketingSnapshotOutput(
            active_campaign_count=s.active_campaign_count,
            spend_last_30d=str(s.spend_last_30d),
            leads_last_24h=s.leads_last_24h,
        )


class RetentionSnapshotOutput(BaseModel):
    at_risk_customers: int
    open_retention_opportunities: int
    open_retention_opportunities_detail: list[dict]
    eligible_review_requests: list[dict]
    reviews_awaiting_marketing_consent: list[dict]
    reviews_ready_for_marketing_content: list[dict]
    negative_feedback_last_24h: list[dict]
    pending_referral_rewards: list[dict]


class GetRetentionSnapshot(Tool):
    name = "insights.get_retention_snapshot"
    description = "Real, read-only retention numbers: at-risk customers, open opportunities, recent negative feedback, pending rewards."
    input_schema = EmptyInput
    output_schema = RetentionSnapshotOutput
    required_permission = Permission.READ_RETENTION

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> RetentionSnapshotOutput:
        s = await self._insight_service.retention_snapshot(context.tenant_id)
        return RetentionSnapshotOutput(
            at_risk_customers=s.at_risk_customers,
            open_retention_opportunities=s.open_retention_opportunities,
            open_retention_opportunities_detail=s.open_retention_opportunities_detail,
            eligible_review_requests=s.eligible_review_requests,
            reviews_awaiting_marketing_consent=s.reviews_awaiting_marketing_consent,
            reviews_ready_for_marketing_content=s.reviews_ready_for_marketing_content,
            negative_feedback_last_24h=s.negative_feedback_last_24h,
            pending_referral_rewards=s.pending_referral_rewards,
        )


class VoiceSnapshotOutput(BaseModel):
    calls_today: int
    new_leads_from_voice_today: int
    human_handoffs_today: int
    unresolved_calls_today: int
    recent_calls: list[dict]


class GetVoiceSnapshot(Tool):
    name = "insights.get_voice_snapshot"
    description = "Real, read-only AI Voice Receptionist activity: calls, new leads, human handoffs, unresolved calls in the last 24h."
    input_schema = EmptyInput
    output_schema = VoiceSnapshotOutput
    required_permission = Permission.READ_VOICE_CALLS

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> VoiceSnapshotOutput:
        s = await self._insight_service.voice_snapshot(context.tenant_id)
        return VoiceSnapshotOutput(
            calls_today=s.calls_today,
            new_leads_from_voice_today=s.new_leads_from_voice_today,
            human_handoffs_today=s.human_handoffs_today,
            unresolved_calls_today=s.unresolved_calls_today,
            recent_calls=s.recent_calls,
        )


class ExceptionSnapshotOutput(BaseModel):
    open_count: int
    high_severity_count: int
    top_open: list[dict]


class GetExceptionSnapshot(Tool):
    name = "insights.get_exception_snapshot"
    description = "Real, read-only open-exception numbers and the most recent open exceptions, across every domain."
    input_schema = EmptyInput
    output_schema = ExceptionSnapshotOutput
    required_permission = Permission.MANAGE_EXCEPTIONS

    def __init__(self, insight_service: InsightService) -> None:
        self._insight_service = insight_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> ExceptionSnapshotOutput:
        s = await self._insight_service.exception_snapshot(context.tenant_id)
        return ExceptionSnapshotOutput(
            open_count=s.open_count, high_severity_count=s.high_severity_count, top_open=s.top_open
        )
