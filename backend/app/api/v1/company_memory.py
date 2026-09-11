"""Company Memory's authenticated CRUD + confirm/reject/revoke API."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_company_memory_service
from app.models.rbac import Permission
from app.services.company_memory_service import (
    CompanyMemoryService,
    MemoryAuthorityError,
    MemoryConcurrentUpdateError,
    MemoryNotFoundError,
    MemoryStateError,
    MemoryValidationError,
)

router = APIRouter(prefix="/memory", tags=["company-memory"])


def _memory_to_dict(m) -> dict[str, Any]:
    return {
        "id": str(m.id), "memory_type": m.memory_type, "key": m.key, "value": m.value,
        "description": m.description, "source": m.source,
        "source_entity_type": m.source_entity_type,
        "source_entity_id": str(m.source_entity_id) if m.source_entity_id else None,
        "created_by": str(m.created_by) if m.created_by else None, "status": m.status,
        "confidence": m.confidence,
        "effective_from": m.effective_from.isoformat() if m.effective_from else None,
        "effective_until": m.effective_until.isoformat() if m.effective_until else None,
        "supersedes_id": str(m.supersedes_id) if m.supersedes_id else None,
        "reason": m.reason, "created_at": m.created_at.isoformat(), "updated_at": m.updated_at.isoformat(),
    }


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, MemoryNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (MemoryValidationError, MemoryStateError, MemoryAuthorityError)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if isinstance(exc, MemoryConcurrentUpdateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


class CreateMemoryRequest(BaseModel):
    memory_type: str
    key: str
    value: str
    description: str | None = None
    source: str = "OWNER_EXPLICIT"
    effective_from: str | None = None
    effective_until: str | None = None
    reason: str | None = None


def _parse_dt(value: str | None):
    if value is None:
        return None
    from datetime import datetime

    return datetime.fromisoformat(value)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: CreateMemoryRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    try:
        memory = await service.create_memory(
            current_user.tenant_id, memory_type=body.memory_type, key=body.key, value=body.value,
            description=body.description, source=body.source, created_by=current_user.id,
            effective_from=_parse_dt(body.effective_from), effective_until=_parse_dt(body.effective_until),
            reason=body.reason,
        )
    except (MemoryValidationError, MemoryAuthorityError, MemoryConcurrentUpdateError) as exc:
        _handle_error(exc)
    return _memory_to_dict(memory)


@router.get("")
async def list_memories(
    memory_type: str | None = None,
    status_filter: str | None = None,
    key: str | None = None,
    source: str | None = None,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    memories = await service.list_memories(
        current_user.tenant_id, memory_type=memory_type, status=status_filter, key=key, source=source,
    )
    return {"memories": [_memory_to_dict(m) for m in memories]}


@router.get("/context")
async def get_context(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    """The exact bounded context AI flows read — registered before
    /{memory_id} so the literal path "context" is never captured by that
    route's UUID path parameter."""
    context = await service.get_context(current_user.tenant_id)
    return {"context": [{"memory_type": e.memory_type, "key": e.key, "value": e.value, "source": e.source} for e in context]}


@router.get("/history/{key}")
async def get_history(
    key: str,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    history = await service.get_history(current_user.tenant_id, key)
    return {"history": [_memory_to_dict(m) for m in history]}


@router.get("/{memory_id}")
async def get_memory(
    memory_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    try:
        memory = await service.get_memory(current_user.tenant_id, memory_id)
    except MemoryNotFoundError as exc:
        _handle_error(exc)
    return _memory_to_dict(memory)


class UpdatePendingMemoryRequest(BaseModel):
    value: str
    description: str | None = None


@router.put("/{memory_id}")
async def update_pending_memory(
    memory_id: uuid.UUID,
    body: UpdatePendingMemoryRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    try:
        memory = await service.update_pending_memory(
            current_user.tenant_id, memory_id, value=body.value, description=body.description,
            updated_by=current_user.id,
        )
    except (MemoryNotFoundError, MemoryValidationError, MemoryStateError) as exc:
        _handle_error(exc)
    return _memory_to_dict(memory)


@router.post("/{memory_id}/confirm")
async def confirm_memory(
    memory_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    try:
        memory = await service.confirm_memory(current_user.tenant_id, memory_id, confirmed_by=current_user.id)
    except (MemoryNotFoundError, MemoryStateError, MemoryAuthorityError, MemoryConcurrentUpdateError) as exc:
        _handle_error(exc)
    return _memory_to_dict(memory)


class RejectMemoryRequest(BaseModel):
    reason: str | None = None


@router.post("/{memory_id}/reject")
async def reject_memory(
    memory_id: uuid.UUID,
    body: RejectMemoryRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    try:
        memory = await service.reject_memory(current_user.tenant_id, memory_id, rejected_by=current_user.id, reason=body.reason)
    except (MemoryNotFoundError, MemoryStateError) as exc:
        _handle_error(exc)
    return _memory_to_dict(memory)


class RevokeMemoryRequest(BaseModel):
    reason: str | None = None


@router.post("/{memory_id}/revoke")
async def revoke_memory(
    memory_id: uuid.UUID,
    body: RevokeMemoryRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_MEMORY)),
    service: CompanyMemoryService = Depends(get_company_memory_service),
) -> dict[str, Any]:
    try:
        memory = await service.revoke_memory(current_user.tenant_id, memory_id, revoked_by=current_user.id, reason=body.reason)
    except (MemoryNotFoundError, MemoryStateError) as exc:
        _handle_error(exc)
    return _memory_to_dict(memory)
