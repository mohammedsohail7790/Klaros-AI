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
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.models.medical_tourism import ReferralCommissionBasis
from app.models.rbac import Permission, role_has_permission
from app.services import medical_tourism_operations
from app.services.medical_tourism_service import InvalidRelationshipError, MedicalTourismService, NotFoundError
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


def _patient_lead_to_dict(pl) -> dict[str, Any]:
    return {
        "id": str(pl.id),
        "lead_id": str(pl.lead_id),
        "procedure_id": str(pl.procedure_id) if pl.procedure_id else None,
        "preferred_destination_country": pl.preferred_destination_country,
        "medical_history_summary": pl.medical_history_summary,
        "travel_start_date": pl.travel_start_date.isoformat() if pl.travel_start_date else None,
        "travel_end_date": pl.travel_end_date.isoformat() if pl.travel_end_date else None,
        "has_insurance": pl.has_insurance,
        "insurance_notes": pl.insurance_notes,
        "created_at": pl.created_at.isoformat(),
    }


def _consultation_to_dict(c) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "appointment_id": str(c.appointment_id),
        "provider_id": str(c.provider_id),
        "procedure_id": str(c.procedure_id) if c.procedure_id else None,
        "status": c.status,
        "notes": c.notes,
        "created_at": c.created_at.isoformat(),
    }


def _referral_commission_to_dict(rc) -> dict[str, Any]:
    return {
        "id": str(rc.id),
        "referral_id": str(rc.referral_id),
        "provider_id": str(rc.provider_id) if rc.provider_id else None,
        "basis": rc.basis,
        "commission_percentage": str(rc.commission_percentage) if rc.commission_percentage is not None else None,
        "flat_amount": str(rc.flat_amount) if rc.flat_amount is not None else None,
        "computed_amount": str(rc.computed_amount) if rc.computed_amount is not None else None,
        "currency": rc.currency,
        "status": rc.status,
        "created_at": rc.created_at.isoformat(),
    }


def _require_manage(current_user: CurrentUser) -> None:
    if current_user.role is None or not role_has_permission(current_user.role, Permission.MANAGE_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: MANAGE_MEDICAL_TOURISM")


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


# --- Patient leads (extends Lead) ------------------------------------------
#
# Not routed through ToolRegistry: per medical_tourism_tools.py's module
# docstring, the PatientLead/Consultation/ReferralCommission extension
# writes are deliberately NOT exposed as agent-callable tools in this phase
# (nothing in the validated walkthrough requires an agent to perform them
# autonomously). These mutating endpoints go straight through the service
# layer with the same manual RBAC check the credential-management endpoints
# above already use.


@router.get("/patient-leads")
async def list_patient_leads(
    procedure_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    patient_leads, total = await service.list_patient_leads(
        current_user.tenant_id, procedure_id=procedure_id, limit=limit, offset=offset
    )
    return {
        "patient_leads": [_patient_lead_to_dict(pl) for pl in patient_leads],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/leads/{lead_id}/operations")
async def get_lead_operations(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The operating context of a patient lead: patient details, provider matches (with the
    reasons for each), consultations, a timeline built from real records, and the next
    action. 404 when the lead is not a patient lead (or not in this tenant)."""
    _require_read(current_user)
    result = await medical_tourism_operations.lead_operations(current_user.tenant_id, lead_id)
    if result is None:
        raise HTTPException(status_code=404, detail="This lead has no patient details")
    return result


@router.get("/leads/{lead_id}/provider-matches")
async def get_lead_provider_matches(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    result = await medical_tourism_operations.match_providers(current_user.tenant_id, lead_id)
    if result is None:
        raise HTTPException(status_code=404, detail="This lead has no patient details")
    return result


@router.get("/patient-leads/{patient_lead_id}")
async def get_patient_lead(
    patient_lead_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    try:
        patient_lead = await service.get_patient_lead(current_user.tenant_id, patient_lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"patient_lead": _patient_lead_to_dict(patient_lead)}


async def _require_consent_for_patient_lead(tenant_id: uuid.UUID, lead_id: uuid.UUID, body: dict) -> None:
    """The patient-lead extension adds health intake to an EXISTING lead: the newest consent evidence for that lead must allow keeping personal data,
    and (when health fields are sent) medical information. A lead with no evidence has no consent (record it first: POST /leads/{id}/consent)."""
    from app.db.session import async_session_maker as maker, set_tenant_context
    from app.models.crm import Lead
    from app.services import consent_gate, halla_consent

    if not await consent_gate.tenant_requires_consent(maker, tenant_id):
        return
    async with maker() as session:
        await set_tenant_context(session, tenant_id)
        lead = await session.get(Lead, lead_id)
        if lead is None or lead.tenant_id != tenant_id:
            return  # the service answers 404
        state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
    health = any(body.get(k) not in (None, "") for k in ("medical_history_summary", "insurance_notes", "procedure_id"))
    missing = [s for s in (consent_gate.STORE_PERSONAL, consent_gate.STORE_MEDICAL if health else None) if s and not state.allows(s)]
    if missing:
        raise HTTPException(status_code=422, detail={"error": "consent_required", "missing_scopes": missing})


@router.post("/patient-leads")
async def create_patient_lead(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """body = {lead_id, procedure_id?, preferred_destination_country?, medical_history_summary?,
    travel_start_date?, travel_end_date?, has_insurance?, insurance_notes?}"""
    _require_manage(current_user)
    service = _service()
    await _require_consent_for_patient_lead(current_user.tenant_id, uuid.UUID(body["lead_id"]), body)
    travel_start_date = date.fromisoformat(body["travel_start_date"]) if body.get("travel_start_date") else None
    travel_end_date = date.fromisoformat(body["travel_end_date"]) if body.get("travel_end_date") else None
    try:
        patient_lead = await service.create_patient_lead(
            current_user.tenant_id,
            uuid.UUID(body["lead_id"]),
            procedure_id=uuid.UUID(body["procedure_id"]) if body.get("procedure_id") else None,
            preferred_destination_country=body.get("preferred_destination_country"),
            medical_history_summary=body.get("medical_history_summary"),
            travel_start_date=travel_start_date,
            travel_end_date=travel_end_date,
            has_insurance=body.get("has_insurance"),
            insurance_notes=body.get("insurance_notes"),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRelationshipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"patient_lead": _patient_lead_to_dict(patient_lead)}


# --- Consultations (extends Appointment) ------------------------------------


@router.get("/consultations")
async def list_consultations(
    provider_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    consultations, total = await service.list_consultations(
        current_user.tenant_id, provider_id=provider_id, status=status, limit=limit, offset=offset
    )
    return {
        "consultations": [_consultation_to_dict(c) for c in consultations],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/consultations/{consultation_id}")
async def get_consultation(
    consultation_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    try:
        consultation = await service.get_consultation(current_user.tenant_id, consultation_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"consultation": _consultation_to_dict(consultation)}


@router.post("/consultations")
async def create_consultation(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """body = {appointment_id, provider_id, procedure_id?, notes?}"""
    _require_manage(current_user)
    service = _service()
    try:
        consultation = await service.create_consultation(
            current_user.tenant_id,
            uuid.UUID(body["appointment_id"]),
            uuid.UUID(body["provider_id"]),
            procedure_id=uuid.UUID(body["procedure_id"]) if body.get("procedure_id") else None,
            notes=body.get("notes"),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRelationshipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"consultation": _consultation_to_dict(consultation)}


@router.patch("/consultations/{consultation_id}")
async def update_consultation(
    consultation_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """body = {status?, notes?}"""
    _require_manage(current_user)
    service = _service()
    try:
        consultation = await service.update_consultation(
            current_user.tenant_id,
            consultation_id,
            status=body.get("status"),
            notes=body.get("notes"),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"consultation": _consultation_to_dict(consultation)}


# --- Referral commissions (extends Referral) ---------------------------------


@router.get("/referral-commissions")
async def list_referral_commissions(
    provider_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    commissions, total = await service.list_referral_commissions(
        current_user.tenant_id, provider_id=provider_id, status=status, limit=limit, offset=offset
    )
    return {
        "referral_commissions": [_referral_commission_to_dict(rc) for rc in commissions],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/referral-commissions/{commission_id}")
async def get_referral_commission(
    commission_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    _require_read(current_user)
    service = _service()
    try:
        commission = await service.get_referral_commission(current_user.tenant_id, commission_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"referral_commission": _referral_commission_to_dict(commission)}


@router.post("/referral-commissions")
async def create_referral_commission(
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """body = {referral_id, currency, provider_id?, basis?, commission_percentage?, flat_amount?}"""
    _require_manage(current_user)
    service = _service()
    try:
        commission = await service.create_referral_commission(
            current_user.tenant_id,
            uuid.UUID(body["referral_id"]),
            currency=body["currency"],
            provider_id=uuid.UUID(body["provider_id"]) if body.get("provider_id") else None,
            basis=body.get("basis", ReferralCommissionBasis.PERCENTAGE),
            commission_percentage=(
                Decimal(str(body["commission_percentage"])) if body.get("commission_percentage") is not None else None
            ),
            flat_amount=Decimal(str(body["flat_amount"])) if body.get("flat_amount") is not None else None,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRelationshipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"referral_commission": _referral_commission_to_dict(commission)}


@router.patch("/referral-commissions/{commission_id}/status")
async def update_referral_commission_status(
    commission_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """body = {status}"""
    _require_manage(current_user)
    service = _service()
    try:
        commission = await service.update_referral_commission_status(
            current_user.tenant_id, commission_id, body["status"]
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"referral_commission": _referral_commission_to_dict(commission)}
