"""Dropshipping store, branch 3: the ONE order tool, `dropshipping.search_orders`.

Read-only, and it returns NO address, email or phone (`include_personal=False`): an order's number, status, totals, the item names and quantities, and the tracking
facts. It is the building block for the AI receptionist's order lookup; how a caller proves they own an order is designed in a later phase.

Per the medical-tourism precedent, no write tool exists just because a staff route does: creating orders, recording payments, supplier orders and tracking are
staff actions through the API (permission checked, audit row written in the same transaction), not something an AI agent performs.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.models.rbac import Permission
from app.services.dropshipping_order_service import DropshippingOrderService
from app.tools.base import ExecutionContext, Tool


class SearchOrdersInput(BaseModel):
    q: str | None = Field(default=None, max_length=40)  # all or part of an order number
    status: str | None = None
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class SearchOrdersOutput(BaseModel):
    orders: list[dict[str, Any]]


class SearchOrders(Tool):
    name = "dropshipping.search_orders"
    description = "Look up orders by order number or status: status, totals, items and tracking. Never returns an address, email or phone. Read-only."
    input_schema = SearchOrdersInput
    output_schema = SearchOrdersOutput
    required_permission = Permission.READ_DROPSHIPPING
    supports_idempotency = True  # SELECT only, verified by reading DropshippingOrderService.search_orders

    def __init__(self, service: DropshippingOrderService) -> None:
        self._service = service

    async def execute(self, input: SearchOrdersInput, context: ExecutionContext) -> SearchOrdersOutput:
        orders = await self._service.search_orders(context.tenant_id, status=input.status, q=input.q, limit=input.limit, offset=input.offset, include_personal=False)
        return SearchOrdersOutput(orders=orders)
