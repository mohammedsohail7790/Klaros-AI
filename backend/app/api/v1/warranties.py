"""Warranty tracking — the "warranty check-in" box from the One-Person
Company diagram's Retention & Referral panel, previously missing from
the codebase entirely.
"""

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/warranties", tags=["warranties"])


class CreateWarrantyRequest(BaseModel):
    customer_id: uuid.UUID
    job_id: uuid.UUID | None = None
    item_description: str
    start_date: date
    expiry_date: date
    notes: str | None = None


@router.post("", status_code=201)
async def create_warranty(
    body: CreateWarrantyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "retention.create_warranty", body.model_dump(mode="json"), execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("")
async def list_warranties(
    customer_id: uuid.UUID | None = None,
    status: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if customer_id:
        payload["customer_id"] = str(customer_id)
    if status:
        payload["status"] = status
    try:
        output = await registry.execute("retention.list_warranties", payload, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class CheckInWarrantyRequest(BaseModel):
    notes: str | None = None


@router.post("/{warranty_id}/check-in")
async def check_in_warranty(
    warranty_id: uuid.UUID,
    body: CheckInWarrantyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"warranty_id": str(warranty_id), **body.model_dump(mode="json")}
    try:
        output = await registry.execute("retention.check_in_warranty", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/detect-expiring")
async def detect_expiring(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("retention.detect_expiring_warranties", {}, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
