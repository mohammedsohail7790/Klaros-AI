"""Phase 3 (Recommendation Engine): the Recommendation API.

Routes:
  GET  /recommendations                    list (filterable by status/type/blueprint_id)
  GET  /recommendations/runs/{run_id}      a specific RecommendationRun envelope
  POST /recommendations/generate           run the engine against the tenant's ACTIVE blueprint
  POST /recommendations/{id}/accept        explicit action endpoint, never generic PATCH
  POST /recommendations/{id}/reject        explicit action endpoint, never generic PATCH

All mutation routes gated by `MANAGE_RECOMMENDATIONS`; reads by
`READ_RECOMMENDATIONS` — mirroring business_blueprint.py's exact
read/manage split convention. Response shapes never expose internal
prompts, credentials, raw model output, or stack traces — only the
recommendation/reason/evidence/provider-or-tool-reference/status/
priority/confidence/implementation_status fields a future UI needs.

Accepting or rejecting a recommendation is a pure status-transition write
to this table — it never itself connects a provider, executes a tool, or
creates an agent (see app/services/recommendation_service.py's module
docstring's "Read-only guarantee").
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_tool_registry
from app.api.tool_deps_recommendations import get_recommendation_service
from app.models.rbac import Permission
from app.services.recommendation_service import (
    InvalidRecommendationTransitionError,
    NoActiveBlueprintError,
    RecommendationNotFoundError,
    RecommendationService,
)
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _recommendation_to_dict(r) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "run_id": str(r.run_id),
        "blueprint_id": str(r.blueprint_id),
        "blueprint_version": r.blueprint_version,
        "type": r.type,
        "capability_key": r.capability_key,
        "provider_key": r.provider_key,
        "provider_implementation_status": r.provider_implementation_status,
        "tool_name": r.tool_name,
        "what": r.what,
        "why": r.why,
        "based_on": r.based_on,
        "dependencies": r.dependencies,
        "cost_estimate": r.cost_estimate,
        "required": r.required,
        "alternatives": r.alternatives,
        "confidence": r.confidence,
        "source": r.source,
        "source_vertical_key": r.source_vertical_key,
        "status": r.status,
        "decided_by": str(r.decided_by) if r.decided_by else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "rejection_reason": r.rejection_reason,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def _run_to_dict(run) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "blueprint_id": str(run.blueprint_id),
        "blueprint_version": run.blueprint_version,
        "status": run.status,
        "triggered_by": str(run.triggered_by) if run.triggered_by else None,
        "verticals_considered": run.verticals_considered,
        "recommendation_count": run.recommendation_count,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat(),
    }


@router.get("")
async def list_recommendations(
    blueprint_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    type_filter: str | None = None,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_RECOMMENDATIONS)),
    service: RecommendationService = Depends(get_recommendation_service),
) -> list[dict[str, Any]]:
    rows = await service.list_recommendations(
        current_user.tenant_id, blueprint_id=blueprint_id, status=status_filter, type_=type_filter
    )
    return [_recommendation_to_dict(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_RECOMMENDATIONS)),
    service: RecommendationService = Depends(get_recommendation_service),
) -> dict[str, Any]:
    try:
        run = await service.get_run(current_user.tenant_id, run_id)
    except RecommendationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _run_to_dict(run)


@router.post("/generate")
async def generate_recommendations(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_RECOMMENDATIONS)),
    service: RecommendationService = Depends(get_recommendation_service),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        run = await service.generate_recommendations(
            current_user.tenant_id, tool_registry=tool_registry, triggered_by=current_user.id
        )
    except NoActiveBlueprintError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    recs = await service.list_recommendations(current_user.tenant_id, blueprint_id=run.blueprint_id)
    return {"run": _run_to_dict(run), "recommendations": [_recommendation_to_dict(r) for r in recs]}


@router.post("/{recommendation_id}/accept")
async def accept_recommendation(
    recommendation_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_RECOMMENDATIONS)),
    service: RecommendationService = Depends(get_recommendation_service),
) -> dict[str, Any]:
    try:
        rec = await service.accept(current_user.tenant_id, recommendation_id, decided_by=current_user.id)
    except RecommendationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidRecommendationTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _recommendation_to_dict(rec)


class RejectRecommendationRequest(BaseModel):
    reason: str | None = None


@router.post("/{recommendation_id}/reject")
async def reject_recommendation(
    recommendation_id: uuid.UUID,
    body: RejectRecommendationRequest = RejectRecommendationRequest(),
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_RECOMMENDATIONS)),
    service: RecommendationService = Depends(get_recommendation_service),
) -> dict[str, Any]:
    try:
        rec = await service.reject(
            current_user.tenant_id, recommendation_id, decided_by=current_user.id, reason=body.reason
        )
    except RecommendationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidRecommendationTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _recommendation_to_dict(rec)
