import uuid
from typing import Any

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.ar_service import ARService
from app.services.collection_service import CollectionService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class AgingOutput(BaseModel):
    current: str
    days_1_30: str
    days_31_60: str
    days_61_90: str
    days_90_plus: str
    total: str


class GetARAging(Tool):
    name = "finance.get_ar_aging"
    description = "Get the tenant's AR aging summary (current / 1-30 / 31-60 / 61-90 / 90+)."
    input_schema = EmptyInput
    output_schema = AgingOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, ar_service: ARService) -> None:
        self._ar_service = ar_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> AgingOutput:
        summary = await self._ar_service.aging_summary(context.tenant_id)
        return AgingOutput(
            current=str(summary.current), days_1_30=str(summary.days_1_30), days_31_60=str(summary.days_31_60),
            days_61_90=str(summary.days_61_90), days_90_plus=str(summary.days_90_plus), total=str(summary.total),
        )


class GetCustomerBalanceInput(BaseModel):
    customer_id: uuid.UUID


class CustomerBalanceOutput(BaseModel):
    customer_id: str
    balance: str


class GetCustomerBalance(Tool):
    name = "finance.get_customer_balance"
    description = "Get a customer's total outstanding AR balance."
    input_schema = GetCustomerBalanceInput
    output_schema = CustomerBalanceOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, ar_service: ARService) -> None:
        self._ar_service = ar_service

    async def execute(self, input: GetCustomerBalanceInput, context: ExecutionContext) -> CustomerBalanceOutput:
        balance = await self._ar_service.customer_balance(context.tenant_id, input.customer_id)
        return CustomerBalanceOutput(customer_id=str(input.customer_id), balance=str(balance))


class DetectOverdueOutput(BaseModel):
    newly_overdue_invoice_ids: list[str]


class DetectOverdueInvoices(Tool):
    name = "finance.detect_overdue_invoices"
    description = "Flip SENT/PARTIALLY_PAID invoices past due_date to OVERDUE and open exceptions + collection actions."
    input_schema = EmptyInput
    output_schema = DetectOverdueOutput
    required_permission = Permission.MANAGE_COLLECTIONS

    def __init__(self, ar_service: ARService) -> None:
        self._ar_service = ar_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectOverdueOutput:
        ids = await self._ar_service.detect_overdue(context.tenant_id)
        return DetectOverdueOutput(newly_overdue_invoice_ids=[str(i) for i in ids])


class ExecuteDueCollectionActionsOutput(BaseModel):
    executed_action_ids: list[str]


class ExecuteDueCollectionActions(Tool):
    """Deterministic, on-demand execution of due `CollectionAction` rows —
    no Temporal `workflow.sleep()` involved."""

    name = "finance.execute_due_collection_actions"
    description = "Execute all PENDING collection actions whose scheduled_for time has passed."
    input_schema = EmptyInput
    output_schema = ExecuteDueCollectionActionsOutput
    required_permission = Permission.MANAGE_COLLECTIONS

    def __init__(self, collection_service: CollectionService) -> None:
        self._collection_service = collection_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> ExecuteDueCollectionActionsOutput:
        ids = await self._collection_service.execute_due_actions(context.tenant_id)
        return ExecuteDueCollectionActionsOutput(executed_action_ids=[str(i) for i in ids])
