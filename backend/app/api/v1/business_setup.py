"""`/api/v1/business-setup/*` — start a business from one form (Medical Tourism first), and read its launch checklist.

Needs the same permission as turning a module on (MANAGE_BUSINESS_JOURNEY). Everything that matters (validation, idempotency, what counts as done) is in
services/mt_setup.py; these routes authenticate, check the permission and translate errors.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, get_current_user, require_permission
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services import mt_setup
from app.services.mt_referrals import ReferralError

router = APIRouter(prefix="/business-setup", tags=["business-setup"])

# What a new business can start from. Only types whose domain functionality exists are listed; nothing is promised that is not built.
BUSINESS_TYPES = [
    {"key": "medical_tourism", "name": "Medical Tourism", "available": True,
     "description": "Connect patients abroad with verified clinics. Klaros handles consent, qualification, provider matching, referrals and commission tracking."},
]


@router.get("/types")
async def list_types(_: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"types": BUSINESS_TYPES}


@router.get("/medical-tourism")
async def get_medical_tourism_setup(u: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return await mt_setup.get_setup(async_session_maker, u.tenant_id)


@router.post("/medical-tourism")
async def post_medical_tourism_setup(body: dict, u: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY))) -> dict[str, Any]:
    try:
        return await mt_setup.apply_setup(async_session_maker, u.tenant_id, u.id, body)
    except ReferralError as exc:
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": str(exc)}) from exc


@router.post("/medical-tourism/confirm")
async def post_confirmation(body: dict, u: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY))) -> dict[str, Any]:
    if not isinstance(body.get("confirmed"), bool):
        raise HTTPException(status_code=422, detail={"error": "confirmed_required"})
    try:
        return await mt_setup.confirm(async_session_maker, u.tenant_id, u.id, str(body.get("key", "")), body["confirmed"])
    except ReferralError as exc:
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": str(exc)}) from exc
