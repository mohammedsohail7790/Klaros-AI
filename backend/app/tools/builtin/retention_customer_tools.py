import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.retention_service import RetentionService
from app.tools.base import ExecutionContext, Tool


class GetCustomerHealthInput(BaseModel):
    customer_id: uuid.UUID


class CustomerHealthOutput(BaseModel):
    customer_id: str
    lifecycle_state: str
    total_jobs: int
    completed_jobs: int
    cancelled_jobs: int
    total_invoiced: str
    total_collected: str
    open_balance: str
    last_completed_job_at: str | None
    last_service_type: str | None
    average_days_between_jobs: float | None
    last_review_request_at: str | None
    last_referral_at: str | None


class GetCustomerHealth(Tool):
    name = "retention.get_customer_health"
    description = "Real, derived customer service history + lifecycle state (never fabricated)."
    input_schema = GetCustomerHealthInput
    output_schema = CustomerHealthOutput
    required_permission = Permission.VIEW_CUSTOMER_HEALTH

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: GetCustomerHealthInput, context: ExecutionContext) -> CustomerHealthOutput:
        h = await self._retention_service.customer_service_history(context.tenant_id, input.customer_id)
        return CustomerHealthOutput(
            customer_id=str(input.customer_id), lifecycle_state=h.lifecycle_state, total_jobs=h.total_jobs,
            completed_jobs=h.completed_jobs, cancelled_jobs=h.cancelled_jobs, total_invoiced=str(h.total_invoiced),
            total_collected=str(h.total_collected), open_balance=str(h.open_balance),
            last_completed_job_at=h.last_completed_job_at.isoformat() if h.last_completed_job_at else None,
            last_service_type=h.last_service_type, average_days_between_jobs=h.average_days_between_jobs,
            last_review_request_at=h.last_review_request_at.isoformat() if h.last_review_request_at else None,
            last_referral_at=h.last_referral_at.isoformat() if h.last_referral_at else None,
        )
