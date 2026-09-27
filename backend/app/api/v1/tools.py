from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import get_tool_registry
from app.models.actor import ActorType
from app.services.tool_catalog_service import list_tool_catalog
from app.tools.base import ExecutionContext
from app.tools.errors import (
    ToolApprovalRequiredError,
    ToolBlockedError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolValidationError,
)
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolSummary(BaseModel):
    name: str
    description: str
    required_permission: str | None


@router.get("", response_model=list[ToolSummary])
async def list_tools(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> list[ToolSummary]:
    context = ExecutionContext(
        tenant_id=current_user.tenant_id,
        actor_type=ActorType.USER,
        actor_id=current_user.id,
        role=current_user.role,
    )
    return [
        ToolSummary(
            name=t.name,
            description=t.description,
            required_permission=t.required_permission.value if t.required_permission else None,
        )
        for t in registry.list_available(context)
    ]


# --- Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.3): read-only tool
# catalog, a thin projection over the existing ToolRegistry. Deliberately
# distinct from `list_tools` above: that endpoint is role-filtered (what
# CAN this caller invoke) and omits `tenant_scoped`/`counts_toward_ai_usage`;
# this one is the full, unfiltered catalog of every registered tool's
# metadata (what tools EXIST at all, regardless of the caller's own role) —
# the shape the Phase 1 plan specifies for Agent-tool-permission-config and
# Recommendation-Engine consumers. See
# app/services/tool_catalog_service.py for the read-through sync design and
# tests/test_tool_catalog_api.py for the proof this can never execute a
# tool or have any side effect. ---


class ToolCatalogEntryResponse(BaseModel):
    name: str
    description: str
    required_permission: str | None
    tenant_scoped: bool
    counts_toward_ai_usage: bool


@router.get("/catalog", response_model=list[ToolCatalogEntryResponse])
async def get_tool_catalog(
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> list[ToolCatalogEntryResponse]:
    del current_user
    return [
        ToolCatalogEntryResponse(
            name=e.name,
            description=e.description,
            required_permission=e.required_permission,
            tenant_scoped=e.tenant_scoped,
            counts_toward_ai_usage=e.counts_toward_ai_usage,
        )
        for e in list_tool_catalog(registry)
    ]


class ExecuteToolRequest(BaseModel):
    input: dict[str, Any] = {}


@router.post("/{tool_name}/execute")
async def execute_tool(
    tool_name: str,
    body: ExecuteToolRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    context = ExecutionContext(
        tenant_id=current_user.tenant_id,
        actor_type=ActorType.USER,
        actor_id=current_user.id,
        role=current_user.role,
    )
    try:
        output = await registry.execute(tool_name, body.input, context)
        return {"status": "success", "output": output.model_dump(mode="json")}
    except ToolNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ToolPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ToolValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ToolBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ToolApprovalRequiredError as exc:
        return {
            "status": "pending_approval",
            "approval_request_id": str(exc.approval_request_id),
        }
