"""Phase 4 (Agent Runtime foundation): the Agent API
(KLAROS_FINAL_API_ARCHITECTURE.md's `/api/v1/agents` table).

Routes:
  GET  /agents                                  list
  POST /agents                                  create (DRAFT)
  GET  /agents/{id}                              read
  PUT  /agents/{id}                              edit a DRAFT agent
  POST /agents/{id}/activate                     DRAFT/PAUSED -> ACTIVE
  POST /agents/{id}/pause                        ACTIVE -> PAUSED
  POST /agents/{id}/archive                      -> ARCHIVED
  GET  /agents/{id}/tool-permissions              list live grants
  POST /agents/{id}/tool-permissions              grant (deny-by-default, validated against ToolRegistry)
  DELETE /agents/{id}/tool-permissions/{tool_name} revoke
  GET  /agents/{id}/versions                      list
  POST /agents/{id}/versions                      create a new DRAFT version
  POST /agents/{id}/versions/{version_id}/publish  DRAFT -> PUBLISHED, agent.current_version_id updated
  POST /agents/{id}/execute                       manually trigger a governed run
  GET  /agents/{id}/executions                    execution history

Lifecycle changes use explicit action endpoints, never a generic PATCH —
matching business_blueprint.py's confirm/reject and recommendations.py's
accept/reject convention. Never trusts a client-supplied tenant_id — every
route derives tenant identity from `CurrentUser`, sourced from the JWT.

Response shapes never expose internal prompts, credentials, or raw
exception details — only the fields a future UI needs.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_tool_registry
from app.api.tool_deps_agents import (
    get_agent_execution_service,
    get_agent_reasoning_service,
    get_agent_service,
)
from app.models.rbac import Permission
from app.services.agent_execution_service import (
    AgentExecutionError,
    AgentNotExecutableError,
    DuplicateExecutionRequestError,
)
from app.services.agent_reasoning_service import AgentReasoningError, AgentReasoningService
from app.services.agent_service import (
    AgentNotFoundError,
    AgentService,
    AgentVersionImmutableError,
    AgentVersionNotFoundError,
    DuplicateToolPermissionError,
    InvalidAgentTransitionError,
    InvalidTriggerConfigError,
    UnknownToolError,
)
from app.services.agent_execution_service import AgentExecutionService
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_to_dict(a) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "name": a.name,
        "purpose": a.purpose,
        "status": a.status,
        "autonomy_tier": a.autonomy_tier,
        "acting_role": a.acting_role,
        "current_version_id": str(a.current_version_id) if a.current_version_id else None,
        "source_blueprint_id": str(a.source_blueprint_id) if a.source_blueprint_id else None,
        "source_blueprint_version": a.source_blueprint_version,
        "source_recommendation_id": str(a.source_recommendation_id) if a.source_recommendation_id else None,
        "created_by": str(a.created_by) if a.created_by else None,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


def _version_to_dict(v) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "agent_id": str(v.agent_id),
        "version": v.version,
        "status": v.status,
        "instructions_snapshot": v.instructions_snapshot,
        "tool_permissions_snapshot": v.tool_permissions_snapshot,
        "memory_refs": v.memory_refs,
        "triggers": v.triggers,
        "max_executions_per_hour": v.max_executions_per_hour,
        "max_concurrent_executions": v.max_concurrent_executions,
        "max_tool_chain_depth": v.max_tool_chain_depth,
        "approval_policy_override": v.approval_policy_override,
        "created_at": v.created_at.isoformat(),
    }


def _grant_to_dict(g) -> dict[str, Any]:
    return {
        "id": str(g.id),
        "agent_id": str(g.agent_id),
        "tool_name": g.tool_name,
        "constraint": g.constraint_config,
        "created_at": g.created_at.isoformat(),
    }


def _execution_to_dict(e) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "agent_id": str(e.agent_id),
        "agent_version_id": str(e.agent_version_id),
        "trigger_source": e.trigger_source,
        "status": e.status,
        # Phase 5: SINGLE_ACTION (Phase 4, unchanged) or REASONING (new
        # bounded multi-step loop) — see app/models/agent.py::AgentExecutionMode.
        "mode": getattr(e, "mode", "SINGLE_ACTION"),
        "tool_name": e.tool_name,
        "tool_input_summary": e.tool_input_summary,
        "result_summary": e.result_summary,
        "error_message": e.error_message,
        "goal": getattr(e, "goal", None),
        "final_response": getattr(e, "final_response", None),
        "termination_reason": getattr(e, "termination_reason", None),
        "step_count": getattr(e, "step_count", 0),
        "approval_request_id": str(e.approval_request_id) if e.approval_request_id else None,
        "started_at": e.started_at.isoformat() if e.started_at else None,
        "completed_at": e.completed_at.isoformat() if e.completed_at else None,
        "created_at": e.created_at.isoformat(),
    }


def _step_to_dict(s) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "execution_id": str(s.execution_id),
        "step_number": s.step_number,
        "step_type": s.step_type,
        "status": s.status,
        "tool_name": s.tool_name,
        "input_summary": s.input_summary,
        "output_summary": s.output_summary,
        # A short, safe, model-provided rationale — never raw
        # chain-of-thought. See AgentDecision.reasoning_summary.
        "decision_summary": s.decision_summary,
        "error_code": s.error_code,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
    }


class CreateAgentRequest(BaseModel):
    name: str
    purpose: str = ""
    autonomy_tier: str = "OBSERVE"


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    purpose: str | None = None
    autonomy_tier: str | None = None


class GrantToolPermissionRequest(BaseModel):
    tool_name: str
    constraint: dict | None = None


class CreateVersionRequest(BaseModel):
    instructions: str
    memory_refs: list[str] = []
    triggers: dict = {}
    max_executions_per_hour: int = 20
    max_concurrent_executions: int = 1
    max_tool_chain_depth: int = 1
    approval_policy_override: str | None = None


class ExecuteAgentRequest(BaseModel):
    # Backward-compatible evolution of Phase 4's single-action request
    # shape (KLAROS_FINAL_API_ARCHITECTURE.md): a request naming
    # `tool_name` behaves EXACTLY as it did in Phase 4 (unchanged
    # AgentExecutionService.run_action, mode=SINGLE_ACTION) — no existing
    # client breaks. A request that instead supplies `goal` (and omits
    # `tool_name`) is new in Phase 5: it starts the bounded, LLM-driven
    # multi-step reasoning loop instead. Supplying both, or neither, is
    # rejected (see execute_agent below) — the two modes are mutually
    # exclusive per request, never silently guessed.
    tool_name: str | None = None
    tool_input: dict = {}
    goal: str | None = None
    idempotency_key: str | None = None


@router.get("")
async def list_agents(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> list[dict[str, Any]]:
    rows = await service.list_agents(current_user.tenant_id, status=status_filter)
    return [_agent_to_dict(a) for a in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: CreateAgentRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    agent = await service.create_agent(
        current_user.tenant_id, name=body.name, purpose=body.purpose, autonomy_tier=body.autonomy_tier,
        acting_role=current_user.role, created_by=current_user.id,
    )
    return _agent_to_dict(agent)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    try:
        agent = await service.get_agent(current_user.tenant_id, agent_id)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _agent_to_dict(agent)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID,
    body: UpdateAgentRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    try:
        agent = await service.update_draft_agent(
            current_user.tenant_id, agent_id, name=body.name, purpose=body.purpose,
            autonomy_tier=body.autonomy_tier, updated_by=current_user.id,
        )
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidAgentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _agent_to_dict(agent)


async def _do_transition(agent_id, current_user, service, action: str) -> dict[str, Any]:
    try:
        agent = await getattr(service, action)(current_user.tenant_id, agent_id, actor_id=current_user.id)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidAgentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _agent_to_dict(agent)


@router.post("/{agent_id}/activate")
async def activate_agent(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    return await _do_transition(agent_id, current_user, service, "activate")


@router.post("/{agent_id}/pause")
async def pause_agent(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    return await _do_transition(agent_id, current_user, service, "pause")


@router.post("/{agent_id}/archive")
async def archive_agent(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    return await _do_transition(agent_id, current_user, service, "archive")


@router.get("/{agent_id}/tool-permissions")
async def list_tool_permissions(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> list[dict[str, Any]]:
    rows = await service.list_tool_permissions(current_user.tenant_id, agent_id)
    return [_grant_to_dict(g) for g in rows]


@router.post("/{agent_id}/tool-permissions", status_code=status.HTTP_201_CREATED)
async def grant_tool_permission(
    agent_id: uuid.UUID,
    body: GrantToolPermissionRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        grant = await service.grant_tool_permission(
            current_user.tenant_id, agent_id, tool_name=body.tool_name, tool_registry=tool_registry,
            constraint_config=body.constraint, created_by=current_user.id,
        )
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnknownToolError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except DuplicateToolPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _grant_to_dict(grant)


@router.delete("/{agent_id}/tool-permissions/{tool_name}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_tool_permission(
    agent_id: uuid.UUID,
    tool_name: str,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> None:
    try:
        await service.revoke_tool_permission(current_user.tenant_id, agent_id, tool_name, actor_id=current_user.id)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{agent_id}/versions")
async def list_versions(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> list[dict[str, Any]]:
    rows = await service.list_versions(current_user.tenant_id, agent_id)
    return [_version_to_dict(v) for v in rows]


@router.post("/{agent_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    agent_id: uuid.UUID,
    body: CreateVersionRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    try:
        version = await service.create_version(
            current_user.tenant_id, agent_id, instructions=body.instructions, memory_refs=body.memory_refs,
            triggers=body.triggers, max_executions_per_hour=body.max_executions_per_hour,
            max_concurrent_executions=body.max_concurrent_executions,
            max_tool_chain_depth=body.max_tool_chain_depth,
            approval_policy_override=body.approval_policy_override, created_by=current_user.id,
        )
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTriggerConfigError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _version_to_dict(version)


@router.post("/{agent_id}/versions/{version_id}/publish")
async def publish_version(
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_AGENTS)),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    try:
        version = await service.publish_version(
            current_user.tenant_id, agent_id, version_id, actor_id=current_user.id
        )
    except (AgentNotFoundError, AgentVersionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AgentVersionImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _version_to_dict(version)


@router.post("/{agent_id}/execute")
async def execute_agent(
    agent_id: uuid.UUID,
    body: ExecuteAgentRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.EXECUTE_AGENT)),
    service: AgentExecutionService = Depends(get_agent_execution_service),
    reasoning_service: AgentReasoningService = Depends(get_agent_reasoning_service),
) -> dict[str, Any]:
    if bool(body.tool_name) == bool(body.goal):
        # Exactly one of tool_name (Phase 4, single-action) / goal (Phase
        # 5, reasoning) must be given — never both, never neither. See
        # ExecuteAgentRequest's own docstring above.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Exactly one of 'tool_name' or 'goal' must be provided",
        )

    if body.tool_name:
        try:
            execution = await service.run_action(
                current_user.tenant_id, agent_id, tool_name=body.tool_name, tool_input=body.tool_input,
                triggered_by=current_user.id, idempotency_key=body.idempotency_key,
            )
        except AgentNotExecutableError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except DuplicateExecutionRequestError as exc:
            existing = await service.get_execution(current_user.tenant_id, exc.existing_execution_id)
            return _execution_to_dict(existing)
        return _execution_to_dict(execution)

    # Phase 5: bounded, LLM-driven multi-step reasoning loop.
    try:
        execution = await reasoning_service.start(
            current_user.tenant_id, agent_id, goal=body.goal, triggered_by=current_user.id,
            idempotency_key=body.idempotency_key,
        )
    except AgentNotExecutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicateExecutionRequestError as exc:
        existing = await reasoning_service.get_execution(current_user.tenant_id, exc.existing_execution_id)
        return _execution_to_dict(existing)
    return _execution_to_dict(execution)


@router.get("/{agent_id}/executions/{execution_id}")
async def get_execution(
    agent_id: uuid.UUID,
    execution_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENT_EXECUTIONS)),
    service: AgentExecutionService = Depends(get_agent_execution_service),
) -> dict[str, Any]:
    try:
        execution = await service.get_execution(current_user.tenant_id, execution_id)
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if execution.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return _execution_to_dict(execution)


@router.get("/{agent_id}/executions/{execution_id}/steps")
async def list_execution_steps(
    agent_id: uuid.UUID,
    execution_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENT_EXECUTIONS)),
    service: AgentExecutionService = Depends(get_agent_execution_service),
    reasoning_service: AgentReasoningService = Depends(get_agent_reasoning_service),
) -> list[dict[str, Any]]:
    try:
        execution = await service.get_execution(current_user.tenant_id, execution_id)
    except AgentExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if execution.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    try:
        steps = await reasoning_service.list_steps(current_user.tenant_id, execution_id)
    except AgentReasoningError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [_step_to_dict(s) for s in steps]


@router.get("/{agent_id}/executions")
async def list_executions(
    agent_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_AGENT_EXECUTIONS)),
    service: AgentService = Depends(get_agent_service),
) -> list[dict[str, Any]]:
    rows = await service.list_executions(current_user.tenant_id, agent_id)
    return [_execution_to_dict(e) for e in rows]
