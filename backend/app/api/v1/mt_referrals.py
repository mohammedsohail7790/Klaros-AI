"""`/api/v1/medical-tourism/*` — provider verification, referrals (with the backend consent gate), handoff tasks, the pipeline and revenue.

Every rule that protects a patient lives in the services (mt_referrals / mt_commission / mt_handoff / mt_pipeline); these routes only authenticate, check the
caller's permission, translate errors to HTTP and shape the answer. The browser is never the control.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker, set_tenant_context
from app.models.medical_tourism import Provider
from app.models.medical_tourism_referral import ProviderReferral, ReferralApproval, ReferralDelivery
from app.models.rbac import Permission, role_has_permission
from app.services import mt_commission, mt_handoff, mt_pipeline, mt_referrals
from app.services.mt_referrals import ReferralError

router = APIRouter(prefix="/medical-tourism", tags=["medical-tourism-referrals"])


def _read(u: CurrentUser) -> None:
    if u.role is None or not role_has_permission(u.role, Permission.READ_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: READ_MEDICAL_TOURISM")


def _manage(u: CurrentUser) -> None:
    if u.role is None or not role_has_permission(u.role, Permission.MANAGE_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: MANAGE_MEDICAL_TOURISM")


def _coord(u: CurrentUser) -> None:
    """A coordinator (STAFF and above) works cases: referrals, approvals, tasks. Provider verification and money need MANAGE."""
    if u.role is None or not role_has_permission(u.role, Permission.COORDINATE_MEDICAL_TOURISM):
        raise HTTPException(status_code=403, detail="Missing permission: COORDINATE_MEDICAL_TOURISM")


def _is_manager(u: CurrentUser) -> bool:
    return u.role is not None and role_has_permission(u.role, Permission.MANAGE_MEDICAL_TOURISM)


def _err(exc: ReferralError | mt_handoff.TaskError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error": exc.code, "message": str(exc)})


def _uuid(value: Any, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail={"error": f"invalid_{name}"}) from None


# --- pipeline ---------------------------------------------------------------


@router.get("/pipeline")
async def get_pipeline(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0), u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _read(u)
    return await mt_pipeline.build_pipeline(async_session_maker, u.tenant_id, limit=limit, offset=offset)


# --- provider verification --------------------------------------------------


@router.get("/providers/{provider_id}/verification")
async def get_verification(provider_id: uuid.UUID, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _read(u)
    async with async_session_maker() as session:
        await set_tenant_context(session, u.tenant_id)
        p = (await session.execute(select(Provider).where(Provider.id == provider_id, Provider.tenant_id == u.tenant_id))).scalar_one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        return {"provider_id": str(p.id), "stored": p.verification_status, "effective": await mt_referrals.effective_verification(session, u.tenant_id, p),
                "referable": await mt_referrals.provider_is_referable(session, u.tenant_id, p)}


@router.put("/providers/{provider_id}/verification")
async def put_verification(provider_id: uuid.UUID, body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _manage(u)
    try:
        p = await mt_referrals.set_verification(async_session_maker, u.tenant_id, provider_id, str(body.get("status", "")), u.id, body.get("note"))
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"provider_id": str(p.id), "verification_status": p.verification_status}


# --- referrals --------------------------------------------------------------


@router.post("/referrals")
async def create_referral(body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    try:
        r, created = await mt_referrals.create_referral(async_session_maker, u.tenant_id, _uuid(body.get("lead_id"), "lead_id"), _uuid(body.get("provider_id"), "provider_id"), u.id,
                                                        _uuid(body["procedure_id"], "procedure_id") if body.get("procedure_id") else None)
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"referral": mt_referrals.referral_to_dict(r), "created": created}


@router.get("/referrals")
async def list_referrals(lead_id: uuid.UUID | None = None, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                         u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _read(u)
    async with async_session_maker() as session:
        await set_tenant_context(session, u.tenant_id)
        q = select(ProviderReferral).where(ProviderReferral.tenant_id == u.tenant_id)
        if lead_id:
            q = q.where(ProviderReferral.lead_id == lead_id)
        if status:
            q = q.where(ProviderReferral.status == status)
        rows = (await session.execute(q.order_by(ProviderReferral.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"referrals": [mt_referrals.referral_to_dict(r) for r in rows]}


@router.get("/referrals/{referral_id}")
async def get_referral(referral_id: uuid.UUID, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """The referral and the gate's CURRENT answer ("would a send be allowed right now, and if not, why"), plus the approval history."""
    _read(u)
    async with async_session_maker() as session:
        await set_tenant_context(session, u.tenant_id)
        r = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == u.tenant_id))).scalar_one_or_none()
        if r is None:
            raise HTTPException(status_code=404, detail="Referral not found")
        gate = await mt_referrals.evaluate_gate(session, u.tenant_id, r)
        approvals = (await session.execute(select(ReferralApproval).where(ReferralApproval.tenant_id == u.tenant_id, ReferralApproval.lead_id == r.lead_id,
                                                                          ReferralApproval.provider_id == r.provider_id).order_by(ReferralApproval.recorded_at.desc()))).scalars().all()
        deliveries = (await session.execute(select(ReferralDelivery).where(ReferralDelivery.tenant_id == u.tenant_id, ReferralDelivery.referral_id == r.id))).scalars().all()
    return {
        "referral": mt_referrals.referral_to_dict(r),
        "gate": {"allowed": gate.allowed, "code": gate.code, "fields": list(gate.fields)},
        "approvals": [{"id": str(a.id), "decision": a.decision, "fields": a.permitted_fields, "patient_informed": a.patient_informed, "wording_version": a.wording_version,
                       "source": a.source, "channel": a.channel, "recorded_by": str(a.recorded_by), "recorded_at": a.recorded_at.isoformat()} for a in approvals],
        "deliveries": [{"id": str(d.id), "transport": d.transport, "fields_sent": d.fields_sent, "sent_at": d.sent_at.isoformat()} for d in deliveries],
    }


@router.post("/referrals/{referral_id}/approval")
async def post_approval(referral_id: uuid.UUID, body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """A coordinator records the PATIENT'S decision about sharing with this provider. Body: decision (GRANTED | DECLINED | WITHDRAWN), permitted_fields[], patient_informed, wording_version, channel?"""
    _coord(u)
    try:
        r, a = await mt_referrals.record_approval(async_session_maker, u.tenant_id, referral_id, u.id, decision=str(body.get("decision", "")).upper(),
                                                  permitted_fields=list(body.get("permitted_fields") or []), patient_informed=body.get("patient_informed") is True,
                                                  wording_version=str(body.get("wording_version") or ""), channel=body.get("channel"))
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"referral": mt_referrals.referral_to_dict(r), "approval_id": str(a.id)}


@router.post("/referrals/{referral_id}/send")
async def post_send(referral_id: uuid.UUID, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    try:
        r, newly = await mt_referrals.send_referral(async_session_maker, u.tenant_id, referral_id, u.id)
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"referral": mt_referrals.referral_to_dict(r), "newly_sent": newly}


@router.post("/referrals/{referral_id}/clinic-response")
async def post_clinic_response(referral_id: uuid.UUID, body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    if not isinstance(body.get("accepted"), bool):
        raise HTTPException(status_code=422, detail={"error": "accepted_required"})
    try:
        r = await mt_referrals.record_clinic_response(async_session_maker, u.tenant_id, referral_id, u.id, accepted=body["accepted"])
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"referral": mt_referrals.referral_to_dict(r)}


@router.post("/referrals/{referral_id}/outcome")
async def post_outcome(referral_id: uuid.UUID, body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    try:
        r = await mt_referrals.record_outcome(async_session_maker, u.tenant_id, referral_id, u.id, outcome=str(body.get("outcome", "")), evidence=str(body.get("evidence", "")))
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"referral": mt_referrals.referral_to_dict(r)}


# --- commission and revenue -------------------------------------------------


@router.post("/referrals/{referral_id}/commission")
async def post_commission(referral_id: uuid.UUID, body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _manage(u)

    def dec(key: str) -> Decimal | None:
        return Decimal(str(body[key])) if body.get(key) is not None else None

    try:
        c = await mt_commission.create_for_referral(async_session_maker, u.tenant_id, referral_id, u.id, currency=str(body.get("currency", "")), basis=str(body.get("basis", "PERCENTAGE")),
                                                    commission_percentage=dec("commission_percentage"), flat_amount=dec("flat_amount"), reference_amount=dec("reference_amount"),
                                                    qualifying_outcome=str(body.get("qualifying_outcome", "TREATMENT_COMPLETED")))
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"commission": mt_commission.commission_to_dict(c)}


@router.post("/commissions/{commission_id}/transition")
async def post_commission_transition(commission_id: uuid.UUID, body: dict, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _manage(u)
    try:
        c = await mt_commission.transition(async_session_maker, u.tenant_id, commission_id, str(body.get("status", "")).upper(), u.id, body.get("note"))
    except ReferralError as exc:
        raise _err(exc) from exc
    return {"commission": mt_commission.commission_to_dict(c)}


@router.get("/revenue")
async def get_revenue(u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _read(u)
    return await mt_commission.revenue_summary(async_session_maker, u.tenant_id)


# --- handoff tasks ----------------------------------------------------------


@router.get("/tasks")
async def list_tasks(status: str | None = None, lead_id: uuid.UUID | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                     u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _read(u)
    rows, total = await mt_handoff.list_tasks(async_session_maker, u.tenant_id, status=status, lead_id=lead_id, limit=limit, offset=offset)
    return {"tasks": [mt_handoff.task_to_dict(t) for t in rows], "total": total}


@router.post("/tasks/{task_id}/accept")
async def accept_task(task_id: uuid.UUID, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    try:
        return {"task": mt_handoff.task_to_dict(await mt_handoff.accept(async_session_maker, u.tenant_id, task_id, u.id))}
    except mt_handoff.TaskError as exc:
        raise _err(exc) from exc


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: uuid.UUID, body: dict | None = None, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    try:
        t = await mt_handoff.complete(async_session_maker, u.tenant_id, task_id, u.id, is_manager=_is_manager(u), note=(body or {}).get("note"))
        return {"task": mt_handoff.task_to_dict(t)}
    except mt_handoff.TaskError as exc:
        raise _err(exc) from exc


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: uuid.UUID, body: dict | None = None, u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _coord(u)
    try:
        t = await mt_handoff.cancel(async_session_maker, u.tenant_id, task_id, u.id, is_manager=_is_manager(u), note=(body or {}).get("note"))
        return {"task": mt_handoff.task_to_dict(t)}
    except mt_handoff.TaskError as exc:
        raise _err(exc) from exc
