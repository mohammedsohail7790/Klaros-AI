import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/workers", tags=["workers"])


class CreateWorkerRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    skills: list[str] = []
    service_types: list[str] = []
    location: str | None = None


@router.post("", status_code=201)
async def create_worker(
    body: CreateWorkerRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute("operations.create_worker", body.model_dump(), execution_context(current_user))
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("")
async def list_workers(
    active_only: bool = Query(default=True),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "operations.list_workers", {"active_only": active_only}, execution_context(current_user)
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class UpdateWorkerStatusRequest(BaseModel):
    status: str


@router.patch("/{worker_id}/status")
async def update_worker_status(
    worker_id: uuid.UUID,
    body: UpdateWorkerStatusRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"worker_id": str(worker_id), "status": body.status}
    try:
        output = await registry.execute("operations.update_worker_status", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
