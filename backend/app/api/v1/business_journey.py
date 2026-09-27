"""Phase 13 (Business Orchestration Foundation): the Business Journey API —
the smallest coherent authenticated surface connecting Discovery ->
Blueprint -> Recommendations into one persisted, resumable journey.

Routes:
  POST /business-journey                         start (idempotent: returns the tenant's existing active journey if any)
  GET  /business-journey                          the tenant's current active journey (also how a client "resumes")
  GET  /business-journey/{id}                     a specific journey, tenant-scoped (active or historical)
  POST /business-journey/{id}/complete-discovery  checkpoint 1: Discovery COMPLETED -> Blueprint review
  POST /business-journey/{id}/confirm-blueprint   checkpoint 2: human confirms -> Blueprint ACTIVE
  POST /business-journey/{id}/generate-recommendations  checkpoint 3: ACTIVE Blueprint -> RecommendationRun
  POST /business-journey/{id}/complete            human-triggered terminal completion
  POST /business-journey/{id}/abandon             human-triggered cancellation

Every forward transition is an explicit named action — never a generic
`POST /transition?from=X&to=Y` — the server (BusinessJourneyService) owns
the legal-transition graph, the client never supplies target state.

All mutation routes gated by MANAGE_BUSINESS_JOURNEY; reads by
READ_BUSINESS_JOURNEY — mirroring business_blueprint.py/recommendations.py's
exact read/manage split convention. Tenant identity always comes from
CurrentUser (the authenticated token), never from a request body field.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_tool_registry
from app.api.tool_deps_business_journey import get_business_journey_service
from app.models.rbac import Permission
from app.services.business_journey_service import (
    BusinessJourneyNotFoundError,
    BusinessJourneyService,
    InvalidJourneyTransitionError,
)
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/business-journey", tags=["business-journey"])


def _journey_to_dict(j) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "status": j.status,
        "discovery_session_id": str(j.discovery_session_id) if j.discovery_session_id else None,
        "blueprint_id": str(j.blueprint_id) if j.blueprint_id else None,
        "recommendation_run_id": str(j.recommendation_run_id) if j.recommendation_run_id else None,
        "created_by": str(j.created_by) if j.created_by else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        "abandoned_at": j.abandoned_at.isoformat() if j.abandoned_at else None,
        "last_error": j.last_error,
        "created_at": j.created_at.isoformat(),
        "updated_at": j.updated_at.isoformat(),
    }


class StartJourneyRequest(BaseModel):
    business_idea: str


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_journey(
    body: StartJourneyRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    if not body.business_idea or not body.business_idea.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="business_idea is required")
    result = await service.start_journey(
        current_user.tenant_id, business_idea=body.business_idea, created_by=current_user.id
    )
    return _journey_to_dict(result.journey)


@router.get("")
async def get_current_journey(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    journey = await service.get_current(current_user.tenant_id)
    if journey is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active business journey for this tenant")
    return _journey_to_dict(journey)


@router.get("/history")
async def list_journeys(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> list[dict[str, Any]]:
    rows = await service.list_journeys(current_user.tenant_id)
    return [_journey_to_dict(j) for j in rows]


@router.get("/{journey_id}")
async def get_journey(
    journey_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    try:
        journey = await service.get_by_id(current_user.tenant_id, journey_id)
    except BusinessJourneyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _journey_to_dict(journey)


@router.post("/{journey_id}/complete-discovery")
async def complete_discovery(
    journey_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    try:
        result = await service.complete_discovery(current_user.tenant_id, journey_id, actor_id=current_user.id)
    except BusinessJourneyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidJourneyTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _journey_to_dict(result.journey)


@router.post("/{journey_id}/confirm-blueprint")
async def confirm_blueprint(
    journey_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    try:
        result = await service.confirm_blueprint(current_user.tenant_id, journey_id, actor_id=current_user.id)
    except BusinessJourneyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidJourneyTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _journey_to_dict(result.journey)


@router.post("/{journey_id}/generate-recommendations")
async def generate_recommendations(
    journey_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        result = await service.generate_recommendations(
            current_user.tenant_id, journey_id, tool_registry=tool_registry, actor_id=current_user.id
        )
    except BusinessJourneyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidJourneyTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _journey_to_dict(result.journey)


@router.post("/{journey_id}/complete")
async def complete_journey(
    journey_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    try:
        result = await service.complete(current_user.tenant_id, journey_id, actor_id=current_user.id)
    except BusinessJourneyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidJourneyTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _journey_to_dict(result.journey)


class AbandonJourneyRequest(BaseModel):
    reason: str | None = None


@router.post("/{journey_id}/abandon")
async def abandon_journey(
    journey_id: uuid.UUID,
    body: AbandonJourneyRequest = AbandonJourneyRequest(),
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
    service: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    try:
        result = await service.abandon(
            current_user.tenant_id, journey_id, actor_id=current_user.id, reason=body.reason
        )
    except BusinessJourneyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidJourneyTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _journey_to_dict(result.journey)
