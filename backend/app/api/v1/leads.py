import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/leads", tags=["leads"])


class CreateLeadRequest(BaseModel):
    name: str
    source: str
    phone: str | None = None
    email: str | None = None
    source_detail: str | None = None
    service_requested: str | None = None
    description: str | None = None
    location: str | None = None
    urgency: str = "MEDIUM"
    estimated_value: float | None = None
    idempotency_key: str | None = None


@router.post("", status_code=201)
async def create_lead(
    body: CreateLeadRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("crm.create_lead", body.model_dump(), execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("")
async def search_leads(
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.search_leads",
            {"status": status, "source": source, "q": q, "limit": limit, "offset": offset},
            execution_context(current_user),
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/{lead_id}")
async def get_lead(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.get_lead", {"lead_id": str(lead_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class UpdateLeadRequest(BaseModel):
    status: str | None = None
    assigned_user_id: uuid.UUID | None = None
    description: str | None = None


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: uuid.UUID,
    body: UpdateLeadRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"lead_id": str(lead_id), **body.model_dump()}
    try:
        output = await registry.execute("crm.update_lead", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{lead_id}/qualify")
async def qualify_lead(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.qualify_lead", {"lead_id": str(lead_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{lead_id}/ai-qualify-advisory")
async def ai_qualify_lead_advisory(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """Phase 12E: a real-LLM-generated recommendation only — never persists
    anything to the lead. Honestly reports `available: false` when no AI
    provider is configured, rather than fabricating a score. To actually
    apply a reviewed recommendation, call POST /{lead_id}/qualify (the
    existing, policy-gated deterministic path) or edit the lead directly."""
    try:
        output = await registry.execute(
            "crm.ai_qualify_lead_advisory", {"lead_id": str(lead_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
