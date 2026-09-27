"""Phase 2 (KLAROS_FINAL_API_ARCHITECTURE.md): the Business Discovery API.

Routes (reconciled shape, see PHASE_2_IMPLEMENTATION_LOG.md):
  POST /business-discovery/sessions              start a session from free text
  POST /business-discovery/sessions/{id}/answer  submit an answer to the current question
  GET  /business-discovery/sessions/{id}         read session state + turns

Gated by READ_BUSINESS_DISCOVERY / MANAGE_BUSINESS_DISCOVERY (this
implementation's own read/manage permission split — MANAGE_BLUEPRINT,
the one permission name literally mandated by
KLAROS_ARCHITECTURE_RECONCILIATION.md #2, gates the Blueprint router
instead — see business_blueprint.py).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_business_discovery import get_business_discovery_service
from app.models.rbac import Permission
from app.services.business_discovery_service import (
    BusinessDiscoveryService,
    DiscoverySessionCompletedError,
    DiscoverySessionNotFoundError,
    DiscoveryTurnResult,
)

router = APIRouter(prefix="/business-discovery", tags=["business-discovery"])


class StartSessionRequest(BaseModel):
    description: str


class AnswerRequest(BaseModel):
    answer: str


class TurnResponse(BaseModel):
    session_id: str
    session_status: str
    questions_asked: int
    turn_sequence: int
    proposed_claim_ids: list[str]
    next_question: str | None
    extraction_available: bool
    extraction_error: str | None

    @classmethod
    def from_result(cls, r: DiscoveryTurnResult) -> "TurnResponse":
        return cls(
            session_id=str(r.session.id),
            session_status=r.session.status,
            questions_asked=r.session.questions_asked,
            turn_sequence=r.turn.sequence,
            proposed_claim_ids=[str(i) for i in r.proposed_claim_ids],
            next_question=r.next_question,
            extraction_available=r.extraction_available,
            extraction_error=r.extraction_error,
        )


def _session_to_dict(s) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "blueprint_id": str(s.blueprint_id) if s.blueprint_id else None,
        "status": s.status,
        "business_idea": s.business_idea,
        "questions_asked": s.questions_asked,
        "max_questions": s.max_questions,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _turn_to_dict(t) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "sequence": t.sequence,
        "kind": t.kind,
        "question": t.question,
        "answer": t.answer,
        "extraction_error": t.extraction_error,
        "created_at": t.created_at.isoformat(),
    }


@router.post("/sessions", response_model=TurnResponse, status_code=status.HTTP_201_CREATED)
async def start_discovery_session(
    body: StartSessionRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_DISCOVERY)),
    service: BusinessDiscoveryService = Depends(get_business_discovery_service),
) -> TurnResponse:
    if not body.description or not body.description.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="description is required")
    result = await service.start_session(
        current_user.tenant_id, business_idea=body.description, created_by=current_user.id
    )
    return TurnResponse.from_result(result)


@router.post("/sessions/{discovery_session_id}/answer", response_model=TurnResponse)
async def answer_discovery_question(
    discovery_session_id: uuid.UUID,
    body: AnswerRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_DISCOVERY)),
    service: BusinessDiscoveryService = Depends(get_business_discovery_service),
) -> TurnResponse:
    if not body.answer or not body.answer.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="answer is required")
    try:
        result = await service.submit_answer(
            current_user.tenant_id, discovery_session_id, answer=body.answer, actor_id=current_user.id
        )
    except DiscoverySessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DiscoverySessionCompletedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TurnResponse.from_result(result)


@router.get("/sessions/{discovery_session_id}")
async def get_discovery_session(
    discovery_session_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_DISCOVERY)),
    service: BusinessDiscoveryService = Depends(get_business_discovery_service),
) -> dict[str, Any]:
    try:
        session = await service.get_session(current_user.tenant_id, discovery_session_id)
        turns = await service.get_turns(current_user.tenant_id, discovery_session_id)
    except DiscoverySessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"session": _session_to_dict(session), "turns": [_turn_to_dict(t) for t in turns]}
