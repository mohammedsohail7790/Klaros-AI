"""Subcontractor/vendor management — the Vendor/VendorBill/Payout models
and finance.* tools (app/tools/builtin/vendor_tools.py) already existed
and were already registered in the ToolRegistry, but no route in this
package ever exposed them: the entire feature was unreachable from the
frontend. Same "backend built, never wired" pattern as everything else
found this session, just one layer earlier (no route file existed at
all, not just a missing frontend wrapper).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/vendors", tags=["vendors"])


class CreateVendorRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None


@router.post("", status_code=201)
async def create_vendor(
    body: CreateVendorRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("finance.create_vendor", body.model_dump(), execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("")
async def list_vendors(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("finance.list_vendors", {}, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class RecordVendorBillRequest(BaseModel):
    vendor_id: uuid.UUID
    job_id: uuid.UUID | None = None
    amount: Decimal
    due_date: date


@router.post("/bills", status_code=201)
async def record_vendor_bill(
    body: RecordVendorBillRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {**body.model_dump(mode="json")}
    try:
        output = await registry.execute("finance.record_vendor_bill", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/bills")
async def list_vendor_bills(
    vendor_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"vendor_id": str(vendor_id)} if vendor_id else {}
    try:
        output = await registry.execute("finance.list_vendor_bills", payload, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class RecordPayoutRequest(BaseModel):
    vendor_id: uuid.UUID
    bill_id: uuid.UUID


@router.post("/bills/{bill_id}/payout")
async def record_payout(
    bill_id: uuid.UUID,
    body: RecordPayoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"vendor_id": str(body.vendor_id), "bill_id": str(bill_id)}
    try:
        output = await registry.execute("finance.record_payout", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
