import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.rbac import Permission
from app.services.consent_gate import ALL_SCOPES, SOURCE_OPERATOR, ConsentRequiredError, tenant_requires_consent
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
    consent: dict[str, Any] | None = None  # {"scopes": [...], "wording_version": "..."} -- required for consent-gated tenants


@router.post("", status_code=201)
async def create_lead(
    body: CreateLeadRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("crm.create_lead", body.model_dump(), execution_context(current_user))
    except (ToolError, ConsentRequiredError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class BulkImportLeadRowRequest(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    service_requested: str | None = None
    description: str | None = None
    location: str | None = None
    estimated_value: float | None = None


@router.post("/import", status_code=201)
async def bulk_import_leads(
    body: list[BulkImportLeadRowRequest],
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.bulk_import_leads",
            {"leads": [row.model_dump() for row in body]},
            execution_context(current_user),
        )
    except (ToolError, ConsentRequiredError) as exc:
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
    except (ToolError, ValueError, ConsentRequiredError) as exc:
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


class AttestConsentRequest(BaseModel):
    scopes: list[str]  # the COMPLETE set of scopes the person has agreed to; an empty list records a withdrawal of everything
    wording_version: str | None = None


@router.post("/{lead_id}/consent")
async def attest_lead_consent(
    lead_id: uuid.UUID,
    body: AttestConsentRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.UPDATE_LEAD)),
) -> dict[str, Any]:
    """A signed-in person records what the lead told them (or that they withdrew). Only for consent-gated tenants; appended to the evidence
    history with the person's user id; the newest decision governs everything (a withdrawal freezes the lead). Agents cannot call this."""
    from app.models.audit_log import AuditLog
    from app.models.crm import Lead
    from app.services import halla_consent

    unknown = [s for s in body.scopes if s not in ALL_SCOPES]
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"error": "unknown_scope", "scopes": unknown})
    if not await tenant_requires_consent(async_session_maker, current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "consent_not_required_for_this_tenant"})
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        lead = await session.get(Lead, lead_id)
        if lead is None or lead.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        await halla_consent.record_snapshot(
            session, current_user.tenant_id, lead.id, granted_scopes=body.scopes, source=SOURCE_OPERATOR,
            wording_version=body.wording_version, actor_user_id=current_user.id,
        )
        session.add(AuditLog(
            tenant_id=current_user.tenant_id, actor_type=ActorType.USER, actor_id=current_user.id, action="lead.consent_recorded", entity_type="lead",
            entity_id=lead.id, input_summary={"scopes": sorted(body.scopes), "source": SOURCE_OPERATOR}, result="success",
        ))
        await session.commit()
        state = await halla_consent.describe_for_lead(session, current_user.tenant_id, lead)
    return state
