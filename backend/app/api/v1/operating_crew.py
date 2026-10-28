"""`/api/v1/operating-crew`: the standing AI crew that runs a tenant's business from its playbook.

GET  /operating-crew            what the playbook contains for this tenant, and which members are already running
POST /operating-crew/provision  create, publish and activate any missing member (idempotent)
PUT  /operating-crew/settings   the tenant's own choices: set the crew up automatically, and which members are switched off

Which playbooks apply comes from the business modules the tenant has enabled (the vertical registry), never from a name or a flag in the request. Provisioning only
uses the ordinary agent service, so every action a crew member takes is still governed by the tool registry, approvals, the audit log and the AI kill switch.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_tool_registry
from app.api.tool_deps_agents import get_agent_service
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services import operating_crew
from app.services.agent_service import AgentService
from app.services.tenant_settings_service import CREW, TenantSettingsService

router = APIRouter(prefix="/operating-crew", tags=["operating-crew"])


def _trigger_text(m: operating_crew.CrewMember) -> str:
    if m.schedule_hourly:
        return "Every hour"
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
    verticals = await operating_crew.enabled_verticals(async_session_maker, current_user.tenant_id)
    settings = await TenantSettingsService(async_session_maker).get(current_user.tenant_id, CREW)
    off = set(settings["disabled_members"])
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
                "switched_off": m.key in off,
                "agent_id": str(a.id) if a else None,
            }
        )
    return {
        "verticals": verticals,
        "members": members,
        "running": sum(1 for x in members if x["running"]),
        "total": len(members),
        "auto_provision": bool(settings["auto_provision"]),
    }


def _plain(text: str | None, limit: int = 180) -> str | None:
    """A short, single-line version of a model's own closing words; nothing technical (tool names, ids, errors) is passed through."""
    if not text:
        return None
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "\u2026"


@router.get("/activity")
async def get_crew_activity(
    days: int = 7,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENT_EXECUTIONS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    """What each crew member last did and how its recent runs went. Read-only: one row per member, newest run first. A failed run is reported as
    "could not finish" without its internal error text, which stays on the agent's own page."""
    days = max(1, min(days, 30))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    verticals = await operating_crew.enabled_verticals(async_session_maker, current_user.tenant_id)
    have = await operating_crew.existing_crew(service, current_user.tenant_id)
    members = [(m, have.get(m.name)) for m in operating_crew.crew_for(verticals)]
    rows = await service.recent_executions_for_agents(current_user.tenant_id, [a.id for _, a in members if a], since=since)
    by_agent: dict[Any, list[Any]] = {}
    for e in rows:
        by_agent.setdefault(e.agent_id, []).append(e)
    out = []
    for m, a in members:
        runs = by_agent.get(a.id, []) if a else []
        last = runs[0] if runs else None
        out.append(
            {
                "key": m.key,
                "name": m.name,
                "agent_id": str(a.id) if a else None,
                "runs": len(runs),
                "failed": sum(1 for e in runs if e.status == "FAILED"),
                "last": None
                if last is None
                else {
                    "status": last.status,
                    "at": (last.completed_at or last.started_at or last.created_at).isoformat(),
                    "trigger": last.trigger_source,
                    "summary": _plain(last.final_response) if last.status == "COMPLETED" else None,
                },
            }
        )
    return {"days": days, "members": out}


@router.post("/provision")
async def provision_operating_crew(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
    registry=Depends(get_tool_registry),
) -> dict[str, Any]:
    verticals = await operating_crew.enabled_verticals(async_session_maker, current_user.tenant_id)
    settings = await TenantSettingsService(async_session_maker).get(current_user.tenant_id, CREW)
    result = await operating_crew.provision_crew(
        service, registry, current_user.tenant_id, current_user.id, verticals, disabled=frozenset(settings["disabled_members"]),
    )
    return {
        "verticals": verticals,
        "created": result.created,
        "already_running": result.already,
        "skipped_tools": result.skipped_tools,
        "failed": result.failed,
        "switched_off": result.switched_off,
    }


class CrewSettingsRequest(BaseModel):
    auto_provision: bool | None = None
    disabled_members: list[str] | None = None


@router.put("/settings")
async def update_crew_settings(
    body: CrewSettingsRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
    registry=Depends(get_tool_registry),
) -> dict[str, Any]:
    """Change the tenant's own crew choices. Switching a member off pauses it at once; switching it back on starts it again."""
    tenant_id = current_user.tenant_id
    known = {m.key: m for crew in operating_crew.PLAYBOOKS.values() for m in crew}
    unknown = [k for k in (body.disabled_members or []) if k not in known]
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown crew member: {', '.join(unknown)}")
    store = TenantSettingsService(async_session_maker)
    current = await store.get(tenant_id, CREW)
    new = {
        "auto_provision": current["auto_provision"] if body.auto_provision is None else body.auto_provision,
        "disabled_members": sorted(set(current["disabled_members"] if body.disabled_members is None else body.disabled_members)),
    }
    await store.set(tenant_id, CREW, new, updated_by=current_user.id)

    have = await operating_crew.existing_crew(service, tenant_id)
    for key in set(new["disabled_members"]) - set(current["disabled_members"]):  # newly switched off: stop it now
        agent = have.get(known[key].name)
        if agent is not None and agent.status == "ACTIVE":
            await service.pause(tenant_id, agent.id, actor_id=current_user.id)
    switched_on = set(current["disabled_members"]) - set(new["disabled_members"])
    for key in switched_on:  # switched back on: start the paused one again, or create it if it never existed
        agent = have.get(known[key].name)
        if agent is not None and agent.status != "ACTIVE" and agent.current_version_id:
            await service.activate(tenant_id, agent.id, actor_id=current_user.id)
    if switched_on:
        verticals = await operating_crew.enabled_verticals(async_session_maker, tenant_id)
        await operating_crew.provision_crew(service, registry, tenant_id, current_user.id, verticals, disabled=frozenset(new["disabled_members"]))
    return {"auto_provision": new["auto_provision"], "disabled_members": new["disabled_members"]}
