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

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_automation_service
from app.api.tool_deps import get_wired_event_bus
from app.api.tool_deps_business_builder import (
    get_business_builder_service,
    get_business_operations_service,
    get_business_workforce_service,
    get_halla_integration_service,
)
from app.events.bus import EventBus
from app.integrations.workforce import (
    WorkforceNotConnectedError,
    WorkforceUnavailableError,
    dev_simulator_enabled,
    halla_enabled,
)
from app.integrations.workforce.events import HallaEventType, InvalidWorkforceEvent, WorkforceInboundEvent
from app.services.business_workforce_service import BusinessWorkforceService, LeadNotFoundError
from app.services.halla_integration_service import (
    HallaIntegrationService,
    HallaNotEnabledError,
    InvalidHallaCredentialError,
    LeadNotCallableError,
)
from app.services.halla_integration_service import LeadNotFoundError as HallaLeadNotFoundError
from app.services.automation_service import AutomationNotPublishedError, AutomationService, AutomationValidationError
from app.services.business_operations_service import ESCALATION_WORKFLOW_NAME, STARTER_WORKFLOW_NAME, BusinessOperationsService
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


@router.get("/workflows/{automation_id}")
async def get_workflow(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    ops: BusinessOperationsService = Depends(get_business_operations_service),
) -> dict[str, Any]:
    detail = await ops.workflow_detail(current_user.tenant_id, automation_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return detail


@router.get("/workforce/setup")
async def get_workforce_setup(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    builder: BusinessBuilderService = Depends(get_business_builder_service),
    journeys: BusinessJourneyService = Depends(get_business_journey_service),
    workforce: BusinessWorkforceService = Depends(get_business_workforce_service),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    """Halla's status exactly as its adapter reports it, plus the business context Klaros
    would hand to it, the setup steps and what each AI team member could do."""
    journey = await journeys.get_current(current_user.tenant_id)
    if journey is None:
        history = await journeys.list_journeys(current_user.tenant_id)
        journey = history[0] if history else None
    overview = await builder.get_overview(current_user.tenant_id, journey=_journey_to_dict(journey) if journey is not None else None)
    result = await workforce.setup(current_user.tenant_id, overview.get("business") or {})
    if halla_enabled():
        result["halla"] = await halla.connection_info(current_user.tenant_id)
    return result


# --- Halla (real AI-workforce integration). Every call is made by the Klaros backend with the TENANT's own
# credential; the browser never sees a credential, a Halla URL or a tenant id it could choose. ---


def _status_view(report) -> dict[str, Any]:
    return {"status": report.status.value, "mode": report.mode, "message": report.message, "adapter_implemented": report.adapter_implemented}


def _halla_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HallaNotEnabledError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The Halla integration is not enabled.")
    if isinstance(exc, WorkforceNotConnectedError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Halla is not connected for this business.")
    if isinstance(exc, WorkforceUnavailableError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, InvalidHallaCredentialError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, (HallaLeadNotFoundError,)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    if isinstance(exc, LeadNotCallableError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    raise exc


_HALLA_ERRORS = (HallaNotEnabledError, WorkforceNotConnectedError, WorkforceUnavailableError, InvalidHallaCredentialError, HallaLeadNotFoundError, LeadNotCallableError)


@router.put("/workforce/halla/connection")
async def connect_halla(
    body: dict[str, Any],
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    """Store this business's Halla credential (encrypted at once) and prove it with a real health request.
    The response carries a status only — never the credential."""
    try:
        report = await halla.connect(
            current_user.tenant_id, current_user.id,
            halla_tenant_id=str(body.get("halla_tenant_id") or ""), api_key=str(body.get("api_key") or ""),
            signing_secret=str(body.get("signing_secret") or ""),
        )
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc
    return {**_status_view(report), "halla": await halla.connection_info(current_user.tenant_id)}


@router.delete("/workforce/halla/connection")
async def disconnect_halla(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    try:
        report = await halla.disconnect(current_user.tenant_id)
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc
    return _status_view(report)


@router.post("/workforce/halla/health")
async def check_halla_health(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    try:
        report = await halla.health(current_user.tenant_id)
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc
    return _status_view(report)


@router.post("/workforce/halla/configure")
async def configure_halla(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    builder: BusinessBuilderService = Depends(get_business_builder_service),
    journeys: BusinessJourneyService = Depends(get_business_journey_service),
    workforce: BusinessWorkforceService = Depends(get_business_workforce_service),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    """Send Klaros' business context to Halla (PUT workforce). Klaros orchestrates; Halla executes."""
    journey = await journeys.get_current(current_user.tenant_id)
    if journey is None:
        history = await journeys.list_journeys(current_user.tenant_id)
        journey = history[0] if history else None
    overview = await builder.get_overview(current_user.tenant_id, journey=_journey_to_dict(journey) if journey is not None else None)
    pack = await workforce.context_pack(current_user.tenant_id, overview.get("business") or {})
    try:
        return await halla.configure(current_user.tenant_id, pack.as_dict())
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc


@router.get("/workforce/halla/agents")
async def list_halla_agents(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY)),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    try:
        return {"agents": await halla.agents(current_user.tenant_id)}
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc


@router.post("/leads/{lead_id}/halla/sync")
async def sync_lead_to_halla(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.UPDATE_LEAD)),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    try:
        return await halla.sync_lead(current_user.tenant_id, lead_id)
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc


@router.post("/leads/{lead_id}/halla/call")
async def ask_halla_to_call(
    lead_id: uuid.UUID,
    body: dict[str, Any] | None = None,
    current_user: CurrentUser = Depends(require_permission(Permission.UPDATE_LEAD)),
    halla: HallaIntegrationService = Depends(get_halla_integration_service),
) -> dict[str, Any]:
    body = body or {}
    try:
        return await halla.call_lead(
            current_user.tenant_id, lead_id, reason=str(body.get("reason") or "follow_up"),
            opening_context=(str(body["opening_context"]) if body.get("opening_context") else None),
        )
    except _HALLA_ERRORS as exc:
        raise _halla_error(exc) from exc


@router.get("/leads")
async def get_lead_board(
    status_filter: str | None = Query(None, alias="status"),
    source: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_LEADS)),
    workforce: BusinessWorkforceService = Depends(get_business_workforce_service),
) -> dict[str, Any]:
    """Leads with the operating columns (priority, assignee, country, service, next action and
    AI-interaction state) in one batched call. Tenant comes from the token only."""
    return await workforce.lead_board(current_user.tenant_id, status=status_filter, source=source, q=q, limit=limit, offset=max(0, offset))


@router.get("/leads/{lead_id}/interaction")
async def get_lead_interaction(
    lead_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_LEADS)),
    workforce: BusinessWorkforceService = Depends(get_business_workforce_service),
) -> dict[str, Any]:
    result = await workforce.lead_interaction(current_user.tenant_id, lead_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return result


@router.post("/workforce/dev/events", status_code=status.HTTP_201_CREATED)
async def simulate_workforce_event(
    body: dict[str, Any],
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    workforce: BusinessWorkforceService = Depends(get_business_workforce_service),
    bus: EventBus = Depends(get_wired_event_bus),
) -> dict[str, Any]:
    """DEVELOPMENT ONLY. Records a workforce event as if Halla had sent it, flagged
    `simulated`. Does not exist (404) unless WORKFORCE_ADAPTER=dev. It is the only way events
    enter today: a real Halla adapter would call the same service."""
    if not dev_simulator_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        event = WorkforceInboundEvent(
            type=HallaEventType(body.get("type", "")),
            lead_id=uuid.UUID(str(body.get("lead_id"))),
            interaction_id=str(body.get("interaction_id") or ""),
            channel=body.get("channel"),
            summary=body.get("summary"),
            outcome=body.get("outcome") or {},
            simulated=True,
        )
        return await workforce.ingest(current_user.tenant_id, event, bus)
    except (ValueError, InvalidWorkforceEvent) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found") from exc


@router.post("/workflows/starter", status_code=status.HTTP_200_OK)
async def create_starter_workflow(
    kind: str = Query("new_lead", pattern="^(new_lead|escalation)$"),
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AUTOMATIONS)),
    automations: AutomationService = Depends(get_automation_service),
) -> dict[str, Any]:
    """Create (and enable) a starter workflow on the EXISTING automation engine. `new_lead`: when a lead is
    created, notify the team. `escalation`: when the AI workforce escalates a lead to a person, notify the
    team. Idempotent — returns the existing one if present. Only an allow-listed action is used, so it can
    really execute; nothing is faked."""
    if kind == "escalation":
        name, description = ESCALATION_WORKFLOW_NAME, "Alert the team when the AI workforce hands a lead to a person."
        trigger_event, title = "halla.lead.escalated", "A lead needs a person"
        body = "The AI workforce escalated a lead. Open Leads to take over."
    else:
        name, description = STARTER_WORKFLOW_NAME, "Alert the team whenever a new lead arrives."
        trigger_event, title = "lead.created", "New lead received"
        body = "A new lead just arrived. Open Leads to review and respond."
    existing = [a for a in await automations.list_automations(current_user.tenant_id) if a.name == name]
    if existing:
        return {"id": str(existing[0].id), "name": existing[0].name, "status": existing[0].status, "created": False}
    try:
        automation = await automations.create_automation(
            current_user.tenant_id,
            name=name,
            description=description,
            trigger_type="EVENT",
            trigger_config={"event_type": trigger_event},
            condition=None,
            steps=[
                {
                    "action": "notifications.create_notification",
                    "params": {"title": title, "body": body, "severity": "INFO", "category": "leads"},
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
