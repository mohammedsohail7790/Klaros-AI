"""Phase 2 (KLAROS_FINAL_API_ARCHITECTURE.md, KLAROS_ARCHITECTURE_
RECONCILIATION.md #1): the Business Blueprint API.

Routes:
  GET  /business-blueprint                       active blueprint + sections
  GET  /business-blueprint/versions/{version}     a specific historical version (read-only)
  PUT  /business-blueprint/sections/{key}         human-edit a section (creates new version if ACTIVE)
  POST /business-blueprint/activate               DRAFT -> ACTIVE (minimum-bar check)
  POST /business-blueprint/claims/{id}/confirm    explicit action endpoint, never generic PATCH
  POST /business-blueprint/claims/{id}/reject     explicit action endpoint, never generic PATCH

All mutation routes gated by `MANAGE_BLUEPRINT` — the exact permission
name KLAROS_ARCHITECTURE_RECONCILIATION.md #2 mandates.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_business_discovery import get_business_blueprint_service
from app.models.business_blueprint import BlueprintSectionKey
from app.models.rbac import Permission
from app.services.business_blueprint_service import (
    BlueprintActivationError,
    BlueprintNotFoundError,
    BusinessBlueprintService,
    ClaimNotFoundError,
    InvalidClaimTransitionError,
)

router = APIRouter(prefix="/business-blueprint", tags=["business-blueprint"])

_VALID_SECTION_KEYS = {k.value for k in BlueprintSectionKey}


def _blueprint_to_dict(b) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "status": b.status,
        "version": b.version,
        "created_by": str(b.created_by) if b.created_by else None,
        "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
        "vertical_extension_id": str(b.vertical_extension_id) if b.vertical_extension_id else None,
        "supersedes_id": str(b.supersedes_id) if b.supersedes_id else None,
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
    }


def _section_to_dict(s) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "section_key": s.section_key,
        "status": s.status,
        "data": s.data,
        "updated_by": str(s.updated_by) if s.updated_by else None,
        "updated_at": s.updated_at.isoformat(),
    }


def _claim_to_dict(c) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "blueprint_id": str(c.blueprint_id),
        "section_key": c.section_key,
        "claim_type": c.claim_type,
        "key": c.key,
        "value": c.value,
        "confidence": c.confidence,
        "provenance": c.provenance,
        "discovery_turn_id": str(c.discovery_turn_id) if c.discovery_turn_id else None,
        "evidence_ref": c.evidence_ref,
        "status": c.status,
        "confirmed_by": str(c.confirmed_by) if c.confirmed_by else None,
        "confirmed_at": c.confirmed_at.isoformat() if c.confirmed_at else None,
        "rejected_reason": c.rejected_reason,
    }


async def _full_blueprint_response(
    service: BusinessBlueprintService, tenant_id: uuid.UUID, blueprint
) -> dict[str, Any]:
    sections = await service.list_sections(tenant_id, blueprint.id)
    claims = await service.list_claims(tenant_id, blueprint.id)
    return {
        "blueprint": _blueprint_to_dict(blueprint),
        "sections": [_section_to_dict(s) for s in sections],
        "claims": [_claim_to_dict(c) for c in claims],
    }


@router.get("")
async def get_active_blueprint(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    blueprint = await service.get_active(current_user.tenant_id)
    if blueprint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active blueprint for this tenant")
    return await _full_blueprint_response(service, current_user.tenant_id, blueprint)


@router.get("/draft")
async def get_draft_blueprint(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    """Not in the literal API-architecture table but needed for a UI to
    review PROPOSED claims before activation — the DRAFT blueprint a
    DiscoverySession is currently filling in, if any."""
    blueprint = await service.get_or_create_draft(current_user.tenant_id, created_by=current_user.id)
    return await _full_blueprint_response(service, current_user.tenant_id, blueprint)


@router.get("/versions/{version}")
async def get_blueprint_version(
    version: int,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    try:
        blueprint = await service.get_version(current_user.tenant_id, version)
    except BlueprintNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _full_blueprint_response(service, current_user.tenant_id, blueprint)


class UpdateSectionRequest(BaseModel):
    blueprint_id: uuid.UUID
    data: dict[str, Any]


@router.put("/sections/{section_key}")
async def update_blueprint_section(
    section_key: str,
    body: UpdateSectionRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    if section_key not in _VALID_SECTION_KEYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown section_key: {section_key}"
        )
    try:
        blueprint, section = await service.update_section(
            current_user.tenant_id,
            body.blueprint_id,
            section_key=section_key,
            data=body.data,
            updated_by=current_user.id,
        )
    except BlueprintNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidClaimTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"blueprint": _blueprint_to_dict(blueprint), "section": _section_to_dict(section)}


class ActivateRequest(BaseModel):
    blueprint_id: uuid.UUID


@router.post("/activate")
async def activate_blueprint(
    body: ActivateRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    try:
        blueprint = await service.activate(current_user.tenant_id, body.blueprint_id, activated_by=current_user.id)
    except BlueprintNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BlueprintActivationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _blueprint_to_dict(blueprint)


@router.post("/claims/{claim_id}/confirm")
async def confirm_claim(
    claim_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    try:
        claim = await service.confirm_claim(current_user.tenant_id, claim_id, confirmed_by=current_user.id)
    except ClaimNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidClaimTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _claim_to_dict(claim)


class RejectClaimRequest(BaseModel):
    reason: str | None = None


@router.post("/claims/{claim_id}/reject")
async def reject_claim(
    claim_id: uuid.UUID,
    body: RejectClaimRequest = RejectClaimRequest(),
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BLUEPRINT)),
    service: BusinessBlueprintService = Depends(get_business_blueprint_service),
) -> dict[str, Any]:
    try:
        claim = await service.reject_claim(
            current_user.tenant_id, claim_id, rejected_by=current_user.id, reason=body.reason
        )
    except ClaimNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidClaimTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _claim_to_dict(claim)
