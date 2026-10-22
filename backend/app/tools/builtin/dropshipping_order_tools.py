"""Dropshipping store, branch 3: the ONE order tool, `dropshipping.search_orders`.

Read-only, and it returns NO address, email or phone (`include_personal=False`): an order's number, status, totals, the item names and quantities, and the tracking
facts. It is the building block for the AI receptionist's order lookup; how a caller proves they own an order is designed in a later phase.

Per the medical-tourism precedent, no write tool exists just because a staff route does: creating orders, recording payments, supplier orders and tracking are
staff actions through the API (permission checked, audit row written in the same transaction), not something an AI agent performs.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.models.rbac import Permission
from app.services.dropshipping_order_lookup import DropshippingOrderLookupService
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


class LookupOrderInput(BaseModel):
    order_number: str = Field(max_length=40)
    email: str | None = Field(default=None, max_length=254)  # the e-mail address the order was placed with
    phone: str | None = Field(default=None, max_length=30)  # or the phone number it was placed with


class LookupOrderOutput(BaseModel):
    found: bool
    message: str | None = None
    order: dict[str, Any] | None = None


class LookupOrder(Tool):
    name = "dropshipping.lookup_order"
    description = (
        "Answer a customer's 'where is my order?'. Needs the order number AND the e-mail address or phone number the order was placed with: ask the customer for both. "
        "Returns found=false (always the same message) when they do not match, and otherwise the order's status, items, total and tracking, with a one-sentence summary to read out. "
        "Never returns an address, e-mail, phone or name. Read-only."
    )
    input_schema = LookupOrderInput
    output_schema = LookupOrderOutput
    required_permission = Permission.READ_DROPSHIPPING
    supports_idempotency = True  # reads, plus a failure record that only limits repeated wrong guesses
    pii_input_fields = ("email", "phone")
    audit_redacts_input = True  # the proof the customer gave is not kept in the audit log

    def __init__(self, service: DropshippingOrderLookupService) -> None:
        self._service = service

    async def execute(self, input: LookupOrderInput, context: ExecutionContext) -> LookupOrderOutput:
        result = await self._service.lookup(context.tenant_id, order_number=input.order_number, email=input.email, phone=input.phone, actor_id=context.actor_id)
        return LookupOrderOutput(**result)
