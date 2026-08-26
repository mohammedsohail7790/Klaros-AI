from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import get_tool_registry
from app.models.actor import ActorType
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
