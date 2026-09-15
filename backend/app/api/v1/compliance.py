"""Business compliance: licenses, insurance policies, bonds, and
certifications with real expiry dates. Matches "The One-Person Company"
diagram's "Licence & liability" input under Human & Owner Input — an
external-authority document only a human can obtain/renew, tracked here
so Klaros can flag it before it lapses instead of the owner finding out
the hard way.
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

router = APIRouter(prefix="/compliance", tags=["compliance"])


class CreateLicenseRequest(BaseModel):
    type: str
    name: str
    license_number: str | None = None
    issuing_authority: str | None = None
    holder_name: str | None = None
    holder_user_id: uuid.UUID | None = None
    issue_date: date | None = None
    expiry_date: date
    document_url: str | None = None
    notes: str | None = None


@router.post("/licenses", status_code=201)
async def create_license(
    body: CreateLicenseRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "compliance.create_license", body.model_dump(mode="json"), execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/licenses")
async def list_licenses(
    status: str | None = None,
    type: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if status:
        payload["status"] = status
    if type:
        payload["type"] = type
    try:
        output = await registry.execute("compliance.list_licenses", payload, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class RenewLicenseRequest(BaseModel):
    issue_date: date | None = None
    expiry_date: date
    document_url: str | None = None


@router.post("/licenses/{license_id}/renew")
async def renew_license(
    license_id: uuid.UUID,
    body: RenewLicenseRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"license_id": str(license_id), **body.model_dump(mode="json")}
    try:
        output = await registry.execute("compliance.renew_license", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/detect-expiring")
async def detect_expiring(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("compliance.detect_expiring", {}, execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
