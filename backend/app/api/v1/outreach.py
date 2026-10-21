"""`/api/v1/outreach`: clinic outreach campaigns (Phase 1: shadow mode only, nothing is ever sent).

Reads need READ_MARKETING; everything that changes anything (create, find contacts, set a contact, start/pause/stop, add a suppression) needs MANAGE_CAMPAIGNS.
An OutreachError is a 409 with its message, an unknown (or another tenant's) campaign is a 404, and an invalid body is a 422.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, require_permission
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services.clinic_outreach_service import ClinicOutreachService, OutreachError, OutreachNotFound
from app.services.outreach_settings import OutreachConfig

router = APIRouter(prefix="/outreach", tags=["outreach"])


def get_outreach_service() -> ClinicOutreachService:
    return ClinicOutreachService(async_session_maker)


class CreateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    config: OutreachConfig
    provider_ids: list[uuid.UUID] | None = None


class SetContactRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class SuppressionRequest(BaseModel):
    value: str = Field(min_length=1, max_length=320)
    kind: str = Field(min_length=1, max_length=10)
    reason: str = Field(default="", max_length=255)


def _raise(exc: OutreachError) -> None:
    code = status.HTTP_404_NOT_FOUND if isinstance(exc, OutreachNotFound) else status.HTTP_409_CONFLICT
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/eligible-clinics")
async def eligible_clinics(
    countries: str = "",
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    codes = [c.strip() for c in countries.split(",") if c.strip()]
    return {"clinics": await service.eligible_clinics(current_user.tenant_id, codes)}


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CreateCampaignRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        campaign_id = await service.create_campaign(current_user.tenant_id, current_user.id, name=body.name, config=body.config, provider_ids=body.provider_ids)
    except OutreachError as exc:
        _raise(exc)
    return {"campaign_id": str(campaign_id)}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.get(current_user.tenant_id, campaign_id)
    except OutreachError as exc:
        _raise(exc)


@router.post("/campaigns/{campaign_id}/find-contacts")
async def find_contacts(
    campaign_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.find_contacts(current_user.tenant_id, campaign_id)
    except OutreachError as exc:
        _raise(exc)


@router.put("/campaigns/{campaign_id}/contacts/{provider_id}")
async def set_contact(
    campaign_id: uuid.UUID,
    provider_id: uuid.UUID,
    body: SetContactRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        await service.set_contact(current_user.tenant_id, campaign_id, provider_id, body.email)
    except OutreachError as exc:
        _raise(exc)
    return {"status": "ok"}


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(
    campaign_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        await service.start_shadow(current_user.tenant_id, current_user.id, campaign_id)
    except OutreachError as exc:
        _raise(exc)
    return {"status": "ok"}


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        await service.pause(current_user.tenant_id, current_user.id, campaign_id)
    except OutreachError as exc:
        _raise(exc)
    return {"status": "ok"}


@router.post("/campaigns/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        await service.stop(current_user.tenant_id, current_user.id, campaign_id)
    except OutreachError as exc:
        _raise(exc)
    return {"status": "ok"}


@router.get("/suppressions")
async def list_suppressions(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    return {"suppressions": await service.list_suppressions(current_user.tenant_id)}


@router.post("/suppressions")
async def add_suppression(
    body: SuppressionRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        await service.suppress(current_user.tenant_id, body.value, body.kind, body.reason)
    except OutreachError as exc:
        _raise(exc)
    return {"status": "ok"}
