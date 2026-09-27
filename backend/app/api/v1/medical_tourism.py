"""Phase 10 (Medical Tourism vertical extension): `/api/v1/medical-tourism/*`.

Follows this codebase's existing FastAPI convention exactly (see
app/api/v1/retention_referrals.py, app/api/v1/customers.py): explicit,
typed routes — never a generic `/entity/{type}` dynamic endpoint — with
mutating calls routed through `ToolRegistry.execute()` (so every write
gets the same permission/tenant/schema/policy/audit enforcement as every
other tool call) and read-only list/get calls querying the DB directly
through the service layer (same split retention_referrals.py/customers.py
already use for their own read paths).
"""

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.models.rbac import Permission, role_has_permission
from app.services.medical_tourism_service import MedicalTourismService, NotFoundError
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/medical-tourism", tags=["medical-tourism"])


def _require_read(current_user: CurrentUser) -> None:
    if current_user.role is None or not role_has_permission(current_user.role, Permission.READ_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: READ_MEDICAL_TOURISM")


def _service() -> MedicalTourismService:
    # Read paths construct the service directly against the app's own
    # session_factory (mirroring app/api/tool_deps.py's own construction
    # of every other *_service for ToolRegistry) rather than the
    # request-scoped `db` session FastAPI's `get_db` dependency provides
    # -- MedicalTourismService's methods each own a short-lived session
    # per call, the same pattern LeadService/ReferralService use.
    return MedicalTourismService(async_session_maker)


def _provider_to_dict(p) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "practitioner_name": p.practitioner_name,
        "country": p.country,
        "city": p.city,
        "address": p.address,
        "contact_email": p.contact_email,
        "contact_phone": p.contact_phone,
        "description": p.description,
        "status": p.status,
        "created_at": p.created_at.isoformat(),
    }


def _procedure_to_dict(p) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "category": p.category,
        "description": p.description,
        "typical_destination_countries": p.typical_destination_countries,
        "status": p.status,
        "created_at": p.created_at.isoformat(),
    }


def _offering_to_dict(o) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "provider_id": str(o.provider_id),
        "procedure_id": str(o.procedure_id),
        "estimated_price": str(o.estimated_price) if o.estimated_price is not None else None,
        "currency": o.currency,
        "status": o.status,
    }


def _credential_to_dict(c) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "provider_id": str(c.provider_id),
        "credential_type": c.credential_type,
        "issuing_authority": c.issuing_authority,
        "credential_number": c.credential_number,
        "status": c.status,
        "verified_at": c.verified_at.isoformat() if c.verified_at else None,
    }


# --- Providers -----------------------------------------------------------


@router.get("/providers")
async def list_providers(
    country: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    providers, total = await service.list_providers(
        current_user.tenant_id, country=country, status=status, limit=limit, offset=offset
    )
    return {"providers": [_provider_to_dict(p) for p in providers], "total": total, "limit": limit, "offset": offset}


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    try:
        provider = await service.get_provider(current_user.tenant_id, provider_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"provider": _provider_to_dict(provider)}


@router.post("/providers")
async def create_provider(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {name, country, practitioner_name?, city?, address?, contact_email?, contact_phone?, description?, idempotency_key?}"""
    try:
        output = await registry.execute("medical_tourism.create_provider", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/providers/{provider_id}/credentials")
async def list_provider_credentials(
    provider_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    credentials = await service.list_provider_credentials(current_user.tenant_id, provider_id)
    return {"credentials": [_credential_to_dict(c) for c in credentials]}


@router.post("/providers/{provider_id}/credentials")
async def add_provider_credential(
    provider_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """body = {credential_type, issuing_authority?, credential_number?, issued_date?, expiry_date?}
    Not routed through ToolRegistry -- credential management is not an
    agent-callable action in this phase (see medical_tourism_tools.py's
    module docstring), but still goes through the same service-layer
    tenant/relationship validation every other write in this router uses."""
    if current_user.role is None or not role_has_permission(current_user.role, Permission.MANAGE_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: MANAGE_MEDICAL_TOURISM")
    service = _service()
    issued_date = date.fromisoformat(body["issued_date"]) if body.get("issued_date") else None
    expiry_date = date.fromisoformat(body["expiry_date"]) if body.get("expiry_date") else None
    try:
        credential = await service.add_provider_credential(
            current_user.tenant_id,
            provider_id,
            credential_type=body["credential_type"],
            issuing_authority=body.get("issuing_authority"),
            credential_number=body.get("credential_number"),
            issued_date=issued_date,
            expiry_date=expiry_date,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"credential": _credential_to_dict(credential)}


@router.post("/providers/{provider_id}/credentials/{credential_id}/verify")
async def verify_provider_credential(
    provider_id: uuid.UUID,
    credential_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if current_user.role is None or not role_has_permission(current_user.role, Permission.MANAGE_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: MANAGE_MEDICAL_TOURISM")
    service = _service()
    try:
        credential = await service.verify_provider_credential(
            current_user.tenant_id, credential_id, current_user.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"credential": _credential_to_dict(credential)}


# --- Procedures ------------------------------------------------------------


@router.get("/procedures")
async def list_procedures(
    category: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    procedures, total = await service.list_procedures(
        current_user.tenant_id, category=category, status=status, limit=limit, offset=offset
    )
    return {
        "procedures": [_procedure_to_dict(p) for p in procedures],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/procedures/{procedure_id}")
async def get_procedure(
    procedure_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    try:
        procedure = await service.get_procedure(current_user.tenant_id, procedure_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"procedure": _procedure_to_dict(procedure)}


@router.post("/procedures")
async def create_procedure(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {name, category?, description?, typical_destination_countries?, idempotency_key?}"""
    try:
        output = await registry.execute("medical_tourism.create_procedure", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


# --- Offerings ---------------------------------------------------------------


@router.get("/offerings")
async def list_offerings(
    provider_id: uuid.UUID | None = None,
    procedure_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    offerings, total = await service.list_offerings(
        current_user.tenant_id,
        provider_id=provider_id,
        procedure_id=procedure_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"offerings": [_offering_to_dict(o) for o in offerings], "total": total, "limit": limit, "offset": offset}


@router.post("/offerings")
async def create_offering(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {provider_id, procedure_id, estimated_price?, currency?}"""
    try:
        output = await registry.execute(
            "medical_tourism.create_provider_offering", body, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
