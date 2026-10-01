"""The Automation Engine's authenticated CRUD + execution-history API."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_automation_service
from app.db.session import async_session_maker, set_tenant_context
from app.models.organization import Organization
from app.models.rbac import Permission
from app.services.automation_schedule import InvalidScheduleError, validate_timezone
from app.services.automation_service import (
    AutomationNotFoundError,
    AutomationNotPublishedError,
    AutomationService,
    AutomationValidationError,
)

router = APIRouter(prefix="/automations", tags=["automations"])


def _automation_to_dict(a) -> dict[str, Any]:
    return {
        "id": str(a.id), "name": a.name, "description": a.description, "status": a.status,
        "published_version_id": str(a.published_version_id) if a.published_version_id else None,
        "created_at": a.created_at.isoformat(),
    }


def _version_to_dict(v) -> dict[str, Any]:
    return {
        "id": str(v.id), "automation_id": str(v.automation_id), "version_number": v.version_number,
        "trigger_type": v.trigger_type, "trigger_config": v.trigger_config, "condition": v.condition,
        "steps": v.steps, "created_at": v.created_at.isoformat(),
    }


def _execution_to_dict(e) -> dict[str, Any]:
    return {
        "id": str(e.id), "automation_id": str(e.automation_id), "automation_version_id": str(e.automation_version_id),
        "trigger_type": e.trigger_type, "status": e.status, "current_step_index": e.current_step_index,
        "context": e.context, "error": e.error, "retry_count": e.retry_count,
        "temporal_workflow_id": e.temporal_workflow_id,
        "started_at": e.started_at.isoformat() if e.started_at else None,
        "completed_at": e.completed_at.isoformat() if e.completed_at else None,
    }


def _step_to_dict(s) -> dict[str, Any]:
    return {
        "id": str(s.id), "step_index": s.step_index, "action": s.action, "status": s.status,
        "result": s.result, "error": s.error,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
    }


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, AutomationNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (AutomationValidationError, AutomationNotPublishedError)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    raise exc


class CreateAutomationRequest(BaseModel):
    name: str
    description: str | None = None
    trigger_type: str
    trigger_config: dict = {}
    condition: dict | None = None
    steps: list[dict]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_automation(
    body: CreateAutomationRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    try:
        automation = await service.create_automation(
            current_user.tenant_id, name=body.name, description=body.description, trigger_type=body.trigger_type,
            trigger_config=body.trigger_config, condition=body.condition, steps=body.steps, created_by=current_user.id,
        )
    except AutomationValidationError as exc:
        _handle_error(exc)
    return _automation_to_dict(automation)


@router.get("")
async def list_automations(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    automations = await service.list_automations(current_user.tenant_id)
    return {"automations": [_automation_to_dict(a) for a in automations]}


@router.get("/summary")
async def get_summary(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    """Real counts for the Owner Cockpit (Rule 20) — registered before
    /{automation_id} so the literal path "summary" is never captured by
    that route's UUID path parameter."""
    return await service.get_summary(current_user.tenant_id)


class TimezoneResponse(BaseModel):
    timezone: str


class UpdateTimezoneRequest(BaseModel):
    timezone: str


@router.get("/timezone", response_model=TimezoneResponse)
async def get_tenant_timezone(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
) -> TimezoneResponse:
    """The tenant's general business timezone (Organization.timezone) —
    what a SCHEDULE-triggered automation's "time" is resolved against.
    Registered before /{automation_id} so the literal path "timezone" is
    never captured by that route's UUID path parameter."""
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        org = await session.get(Organization, current_user.tenant_id)
        return TimezoneResponse(timezone=org.timezone if org else "UTC")


@router.put("/timezone", response_model=TimezoneResponse)
async def update_tenant_timezone(
    body: UpdateTimezoneRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
) -> TimezoneResponse:
    try:
        validate_timezone(body.timezone)
    except InvalidScheduleError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        org = await session.get(Organization, current_user.tenant_id)
        org.timezone = body.timezone
        await session.commit()
        return TimezoneResponse(timezone=org.timezone)


@router.post("/scheduled/dispatch-tick")
async def dispatch_scheduled_tick(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    """Manual "run the scheduler tick now" override (Rule 19), following
    the exact same precedent as POST /events/process/{event_type}
    (app/api/v1/events.py): the background worker already runs this same
    idempotent check every poll tick, so this only forces an immediate
    pass rather than waiting — safe to leave enabled in every environment
    since it can never do anything the next real tick wouldn't also do,
    and it is scoped to the caller's own tenant only (tenant A can never
    dispatch tenant B's schedule through this endpoint)."""
    dispatched = await service.check_and_dispatch_scheduled(current_user.tenant_id)
    return {"dispatched_execution_ids": [str(e) for e in dispatched]}


@router.get("/{automation_id}")
async def get_automation(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    try:
        automation = await service.get_automation(current_user.tenant_id, automation_id)
    except AutomationNotFoundError as exc:
        _handle_error(exc)
    next_run = await service.get_next_run(current_user.tenant_id, automation_id)
    return {**_automation_to_dict(automation), "next_scheduled_run": next_run.isoformat() if next_run else None}


@router.get("/{automation_id}/versions")
async def list_versions(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    versions = await service.list_versions(current_user.tenant_id, automation_id)
    return {"versions": [_version_to_dict(v) for v in versions]}


class UpdateAutomationRequest(BaseModel):
    trigger_type: str
    trigger_config: dict = {}
    condition: dict | None = None
    steps: list[dict]


@router.put("/{automation_id}")
async def update_automation(
    automation_id: uuid.UUID,
    body: UpdateAutomationRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    try:
        version = await service.update_automation(
            current_user.tenant_id, automation_id, trigger_type=body.trigger_type, trigger_config=body.trigger_config,
            condition=body.condition, steps=body.steps, created_by=current_user.id,
        )
    except (AutomationNotFoundError, AutomationValidationError) as exc:
        _handle_error(exc)
    return _version_to_dict(version)


@router.post("/{automation_id}/publish")
async def publish_automation(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    try:
        automation = await service.publish(current_user.tenant_id, automation_id)
    except (AutomationNotFoundError, AutomationValidationError) as exc:
        _handle_error(exc)
    return _automation_to_dict(automation)


class SetEnabledRequest(BaseModel):
    enabled: bool


@router.post("/{automation_id}/enabled")
async def set_enabled(
    automation_id: uuid.UUID,
    body: SetEnabledRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    try:
        automation = await service.set_enabled(current_user.tenant_id, automation_id, body.enabled)
    except (AutomationNotFoundError, AutomationNotPublishedError) as exc:
        _handle_error(exc)
    return _automation_to_dict(automation)


class TriggerManualRequest(BaseModel):
    entity_type: str | None = None
    entity_id: str | None = None
    context: dict = {}


@router.post("/{automation_id}/trigger")
async def trigger_manual(
    automation_id: uuid.UUID,
    body: TriggerManualRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    # entity_id must be a real UUID before it ever reaches
    # AutomationExecution.entity_id (a Uuid(as_uuid=True) column) — passed
    # to trigger_manual as its own keyword, never derived from the
    # free-form `context` JSON blob (which keeps entity_id as the plain
    # string the caller supplied, unchanged, for step templating).
    try:
        parsed_entity_id = uuid.UUID(body.entity_id) if body.entity_id else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"invalid entity_id: {exc}") from exc
    # entity_type/entity_id are also kept in the stored context blob (as
    # the original strings) so any `{{entity_type}}`/`{{entity_id}}` step
    # templating keeps working exactly as before this fix.
    context = {**body.context, "entity_type": body.entity_type, "entity_id": body.entity_id}
    try:
        execution = await service.trigger_manual(
            current_user.tenant_id, automation_id, context=context, triggered_by=current_user.id,
            entity_type=body.entity_type, entity_id=parsed_entity_id,
        )
    except (AutomationNotFoundError, AutomationNotPublishedError) as exc:
        _handle_error(exc)
    if execution is None:
        return {"deduplicated": True}
    return _execution_to_dict(execution)


@router.get("/{automation_id}/executions")
async def list_executions(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    executions = await service.list_executions(current_user.tenant_id, automation_id)
    return {"executions": [_execution_to_dict(e) for e in executions]}


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AUTOMATIONS)),
    service: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    try:
        execution = await service.get_execution(current_user.tenant_id, execution_id)
    except AutomationNotFoundError as exc:
        _handle_error(exc)
    steps = await service.list_execution_steps(current_user.tenant_id, execution_id)
    return {**_execution_to_dict(execution), "steps": [_step_to_dict(s) for s in steps]}
