"""Consent wording for public forms: the owner reads, edits and approves the exact words shown beside the consent boxes.

  GET  /consent-wordings   every approved version (newest first) and a plain-language DRAFT the owner can start from
  POST /consent-wordings   approve a wording; creates the next immutable version (WEB-v1, WEB-v2, ...). A wording is never edited.

Reads need READ_BUSINESS_JOURNEY; approving needs MANAGE_INTEGRATIONS (owner or admin). Tenant identity comes only from the authenticated token.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, require_permission
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.models.rbac import Permission
from app.services import consent_wording_service as svc
from sqlalchemy import select

router = APIRouter(prefix="/consent-wordings", tags=["consent-wordings"])


async def _business_name(tenant_id) -> str:
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        org = (await session.execute(select(Organization).where(Organization.id == tenant_id))).scalar_one_or_none()
    return org.name if org is not None else ""


@router.get("")
async def list_consent_wordings(current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY))) -> dict[str, Any]:
    wordings = await svc.list_wordings(async_session_maker, current_user.tenant_id)
    return {"wordings": wordings, "suggested_text": svc.suggested_text(await _business_name(current_user.tenant_id)), "min_length": svc.MIN_LENGTH, "max_length": svc.MAX_LENGTH}


@router.post("", status_code=201)
async def approve_consent_wording(body: dict[str, Any], current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS))) -> dict[str, Any]:
    try:
        return await svc.approve(async_session_maker, current_user.tenant_id, current_user.id, text=body.get("text"), language=body.get("language") or "en")
    except svc.ConsentWordingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
