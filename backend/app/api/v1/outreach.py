"""`/api/v1/outreach`: clinic outreach campaigns (Phase 1: shadow mode only, nothing is ever sent).

Reads need READ_MARKETING; everything that changes anything (create, find contacts, set a contact, start/pause/stop, add a suppression) needs MANAGE_CAMPAIGNS.
An OutreachError is a 409 with its message, an unknown (or another tenant's) campaign is a 404, and an invalid body is a 422.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
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


class BlocksRequest(BaseModel):
    blocks: dict[str, str]


class ReviewRequest(BaseModel):
    mark: str | None = Field(default=None, pattern="^(GOOD|FLAGGED)$")
    reason: str | None = Field(default=None, max_length=500)


class TryReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    provider_id: uuid.UUID | None = None


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
    limit: int = Query(default=5, ge=1, le=10),
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.find_contacts(current_user.tenant_id, campaign_id, limit=limit)
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
        await service.set_contact(current_user.tenant_id, campaign_id, provider_id, body.email, user_id=current_user.id)
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


# --- wording blocks and shadow review (Phase 2: drafts only, nothing is sent) --------------------------------------------


def _unprocessable(exc: OutreachError) -> None:
    problems = exc.args[1] if len(exc.args) > 1 else None
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problems or str(exc)) from exc


@router.get("/blocks")
async def get_blocks(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    return await service.get_blocks(current_user.tenant_id)


@router.put("/blocks")
async def save_blocks(
    body: BlocksRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.save_blocks(current_user.tenant_id, current_user.id, body.blocks)
    except OutreachError as exc:
        _unprocessable(exc)


@router.post("/blocks/approve")
async def approve_blocks(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.approve_blocks(current_user.tenant_id, current_user.id)
    except OutreachError as exc:
        _raise(exc)


@router.get("/campaigns/{campaign_id}/messages")
async def list_messages(
    campaign_id: uuid.UUID,
    stage: str | None = None,
    message_status: str | None = Query(default=None, alias="status"),
    review: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.list_messages(current_user.tenant_id, campaign_id, stage=stage, status=message_status, review=review, limit=limit, offset=offset)
    except OutreachError as exc:
        _raise(exc)


@router.get("/campaigns/{campaign_id}/messages/{message_id}")
async def get_message(
    campaign_id: uuid.UUID,
    message_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.get_message(current_user.tenant_id, campaign_id, message_id)
    except OutreachError as exc:
        _raise(exc)


@router.put("/campaigns/{campaign_id}/messages/{message_id}/review")
async def review_message(
    campaign_id: uuid.UUID,
    message_id: uuid.UUID,
    body: ReviewRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        await service.review_message(current_user.tenant_id, current_user.id, campaign_id, message_id, body.mark, body.reason)
    except OutreachNotFound as exc:
        _raise(exc)
    except OutreachError as exc:
        _unprocessable(exc)
    return {"status": "ok"}


@router.get("/campaigns/{campaign_id}/clinics/{provider_id}/preview-sequence")
async def preview_sequence(
    campaign_id: uuid.UUID,
    provider_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return {"messages": await service.preview_sequence(current_user.tenant_id, campaign_id, provider_id)}
    except OutreachError as exc:
        _raise(exc)


@router.get("/campaigns/{campaign_id}/shadow-progress")
async def shadow_progress(
    campaign_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MARKETING)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    try:
        return await service.shadow_progress(current_user.tenant_id, campaign_id)
    except OutreachError as exc:
        _raise(exc)


@router.post("/campaigns/{campaign_id}/try-reply")
async def try_reply(
    campaign_id: uuid.UUID,
    body: TryReplyRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_CAMPAIGNS)),
    service: ClinicOutreachService = Depends(get_outreach_service),
) -> dict[str, Any]:
    """Classify a pasted clinic reply and show the answer that would be drafted. Stores nothing about any clinic and sends nothing."""
    try:
        return await service.try_reply(current_user.tenant_id, current_user.id, campaign_id, body.text, body.provider_id)
    except OutreachError as exc:
        _raise(exc)
