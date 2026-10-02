"""Business Builder API — the read-only product views derived from the
tenant's Business Blueprint, plus the explicit "enable an industry module"
action and the AI-workforce boundary status.

Routes:
  GET  /business-builder/overview                    requirements, business map, next actions,
                                                     launch checklist, stage tracker, workforce
  GET  /business-builder/workforce                   AI workforce boundary status
  POST /business-builder/modules/{key}/enable        explicit human action: opt this business
                                                     into an industry module from the registry

Nothing here writes business facts: every view is recomputed from the
Blueprint/Recommendations/Website/connection rows
(app/services/business_builder_service.py). Reads need READ_BUSINESS_JOURNEY,
the one write needs MANAGE_BUSINESS_JOURNEY; tenant identity comes only from
the authenticated token.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_automation_service
from app.api.tool_deps_business_builder import get_business_builder_service, get_business_operations_service
from app.services.automation_service import AutomationNotPublishedError, AutomationService, AutomationValidationError
from app.services.business_operations_service import STARTER_WORKFLOW_NAME, BusinessOperationsService
from app.api.tool_deps_business_journey import get_business_journey_service
from app.api.v1.business_journey import _journey_to_dict
from app.models.rbac import Permission
from app.services.business_builder_service import BusinessBuilderService
from app.services.business_journey_service import BusinessJourneyService
from app.services.vertical_extension_service import (
    VerticalExtensionAlreadyExistsError,
    VerticalExtensionNotFoundError,
    VerticalExtensionService,
)
from app.db.session import async_session_maker
from app.models.vertical_extension import VerticalExtensionStatus

router = APIRouter(prefix="/business-builder", tags=["business-builder"])


@router.get("/overview")
async def get_overview(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    builder: BusinessBuilderService = Depends(get_business_builder_service),
    journeys: BusinessJourneyService = Depends(get_business_journey_service),
) -> dict[str, Any]:
    journey = await journeys.get_current(current_user.tenant_id)
    if journey is None:
        # A finished or abandoned journey is still useful context for the
        # operating home; fall back to the most recent one.
        history = await journeys.list_journeys(current_user.tenant_id)
        journey = history[0] if history else None
    return await builder.get_overview(
        current_user.tenant_id, journey=_journey_to_dict(journey) if journey is not None else None
    )


@router.get("/workforce")
async def get_workforce(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    builder: BusinessBuilderService = Depends(get_business_builder_service),
) -> dict[str, Any]:
    return await builder.get_workforce(current_user.tenant_id)


@router.get("/operations")
async def get_operations(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    ops: BusinessOperationsService = Depends(get_business_operations_service),
) -> dict[str, Any]:
    """Everything the operating console shows, computed from real records (leads, workflows,
    agents, integrations, activity, and each enabled industry module's own metrics)."""
    return await ops.get_operations(current_user.tenant_id)


@router.post("/workflows/starter", status_code=status.HTTP_200_OK)
async def create_starter_workflow(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    automations: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    """Create (and enable) the "New lead alert" workflow on the EXISTING automation engine:
    when a lead is created, notify the team. Idempotent — returns the existing one if present.
    Only an allow-listed action is used, so it can really execute; nothing is faked."""
    existing = [a for a in await automations.list_automations(current_user.tenant_id) if a.name == STARTER_WORKFLOW_NAME]
    if existing:
        return {"id": str(existing[0].id), "name": existing[0].name, "status": existing[0].status, "created": False}
    try:
        automation = await automations.create_automation(
            current_user.tenant_id,
            name=STARTER_WORKFLOW_NAME,
            description="Alert the team whenever a new lead arrives.",
            trigger_type="EVENT",
            trigger_config={"event_type": "lead.created"},
            condition=None,
            steps=[
                {
                    "action": "notifications.create_notification",
                    "params": {
                        "title": "New lead received",
                        "body": "A new lead just arrived. Open Leads to review and respond.",
                        "severity": "INFO",
                        "category": "leads",
                    },
                }
            ],
            created_by=current_user.id,
        )
        await automations.publish(current_user.tenant_id, automation.id)
        automation = await automations.set_enabled(current_user.tenant_id, automation.id, True)
    except (AutomationValidationError, AutomationNotPublishedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"id": str(automation.id), "name": automation.name, "status": automation.status, "created": True}


@router.post("/modules/{key}/enable", status_code=status.HTTP_200_OK)
async def enable_module(
    key: str,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY)),
) -> dict[str, Any]:
    verticals = VerticalExtensionService(async_session_maker)
    try:
        vertical = await verticals.get_by_key(key)
    except VerticalExtensionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if vertical.status == VerticalExtensionStatus.DISABLED.value or not (vertical.capabilities or []):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The {vertical.name} module has no domain functionality to enable yet.",
        )
    try:
        await verticals.enable_for_organization(current_user.tenant_id, key, enabled_by=current_user.id)
    except VerticalExtensionAlreadyExistsError:
        pass  # idempotent: already enabled
    return {"key": vertical.key, "name": vertical.name, "enabled": True}
