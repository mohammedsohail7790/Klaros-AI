"""`/api/v1/operating-crew`: the standing AI crew that runs a tenant's business from its playbook.

GET  /operating-crew            what the playbook contains for this tenant, and which members are already running
POST /operating-crew/provision  create, publish and activate any missing member (idempotent)

Which playbooks apply comes from the business modules the tenant has enabled (the vertical registry), never from a name or a flag in the request. Provisioning only
uses the ordinary agent service, so every action a crew member takes is still governed by the tool registry, approvals, the audit log and the AI kill switch.
"""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_tool_registry
from app.api.tool_deps_agents import get_agent_service
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services import operating_crew
from app.services.agent_service import AgentService
from app.services.vertical_extension_service import VerticalExtensionService

router = APIRouter(prefix="/operating-crew", tags=["operating-crew"])


async def _verticals(tenant_id) -> list[str]:
    service = VerticalExtensionService(async_session_maker)
    enabled: list[str] = []
    for key in operating_crew.PLAYBOOKS:
        if key == "generic":
            continue
        try:
            if await service.is_enabled_for_organization(tenant_id, key):
                enabled.append(key)
        except Exception:  # a missing catalogue row means the module is not enabled
            continue
    return enabled


def _trigger_text(m: operating_crew.CrewMember) -> str:
    if m.event_type and m.schedule_time:
        return f"When {m.event_type} happens, and daily at {m.schedule_time}"
    if m.event_type:
        return f"When {m.event_type} happens"
    return f"Every day at {m.schedule_time}"


@router.get("")
async def get_operating_crew(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    verticals = await _verticals(current_user.tenant_id)
    have = await operating_crew.existing_crew(service, current_user.tenant_id)
    members = []
    for m in operating_crew.crew_for(verticals):
        a = have.get(m.name)
        members.append(
            {
                "key": m.key,
                "name": m.name,
                "purpose": m.purpose,
                "trigger": _trigger_text(m),
                "status": (a.status if a else "NOT_SET_UP"),
                "running": bool(a and a.status == "ACTIVE" and a.current_version_id),
                "agent_id": str(a.id) if a else None,
            }
        )
    return {"verticals": verticals, "members": members, "running": sum(1 for x in members if x["running"]), "total": len(members)}


@router.post("/provision")
async def provision_operating_crew(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
    registry=Depends(get_tool_registry),
) -> dict[str, Any]:
    verticals = await _verticals(current_user.tenant_id)
    result = await operating_crew.provision_crew(service, registry, current_user.tenant_id, current_user.id, verticals)
    return {
        "verticals": verticals,
        "created": result.created,
        "already_running": result.already,
        "skipped_tools": result.skipped_tools,
        "failed": result.failed,
    }
