from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.retention_service import RetentionService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class DetectAtRiskOutput(BaseModel):
    changed_customer_ids: list[str]


class DetectAtRiskAndInactive(Tool):
    name = "retention.detect_at_risk_and_inactive"
    description = "Deterministically flip overdue/inactive customers to AT_RISK/INACTIVE and open CUSTOMER_AT_RISK exceptions."
    input_schema = EmptyInput
    output_schema = DetectAtRiskOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectAtRiskOutput:
        ids = await self._retention_service.detect_at_risk_and_inactive(context.tenant_id)
        return DetectAtRiskOutput(changed_customer_ids=[str(i) for i in ids])


class DetectPaymentRiskOutput(BaseModel):
    flagged_customer_ids: list[str]


class DetectPaymentIssueRisk(Tool):
    name = "retention.detect_payment_issue_risk"
    description = "Deterministically flag customers with overdue invoices beyond tolerance as PAYMENT_ISSUE risk."
    input_schema = EmptyInput
    output_schema = DetectPaymentRiskOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectPaymentRiskOutput:
        ids = await self._retention_service.detect_payment_issue_risk(context.tenant_id)
        return DetectPaymentRiskOutput(flagged_customer_ids=[str(i) for i in ids])


class IdentifyAdvocatesOutput(BaseModel):
    candidate_customer_ids: list[str]


class IdentifyAdvocateCandidates(Tool):
    name = "retention.identify_advocate_candidates"
    description = "Deterministically identify advocate candidates (repeat jobs, no complaints, no overdue invoices)."
    input_schema = EmptyInput
    output_schema = IdentifyAdvocatesOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> IdentifyAdvocatesOutput:
        ids = await self._retention_service.identify_advocate_candidates(context.tenant_id)
        return IdentifyAdvocatesOutput(candidate_customer_ids=[str(i) for i in ids])
