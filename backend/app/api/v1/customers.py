import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/customers", tags=["customers"])


class CreateCustomerRequest(BaseModel):
    name: str
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


@router.post("", status_code=201)
async def create_customer(
    body: CreateCustomerRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.create_customer", body.model_dump(), execution_context(current_user)
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/import", status_code=201)
async def bulk_import_customers(
    body: list[CreateCustomerRequest],
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.bulk_import_customers",
            {"customers": [c.model_dump() for c in body]},
            execution_context(current_user),
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("")
async def search_customers(
    q: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.search_customers", {"q": q, "limit": limit, "offset": offset}, execution_context(current_user)
        )
    except ToolError as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/{customer_id}")
async def get_customer(
    customer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.get_customer", {"customer_id": str(customer_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class UpdateCustomerRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    status: str | None = None
    notes: str | None = None


@router.patch("/{customer_id}")
async def update_customer(
    customer_id: uuid.UUID,
    body: UpdateCustomerRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"customer_id": str(customer_id), **body.model_dump()}
    try:
        output = await registry.execute("crm.update_customer", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class CreateNoteRequest(BaseModel):
    body: str


@router.post("/{customer_id}/notes", status_code=201)
async def create_note(
    customer_id: uuid.UUID,
    body: CreateNoteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"customer_id": str(customer_id), "body": body.body}
    try:
        output = await registry.execute("crm.create_note", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/{customer_id}/timeline")
async def get_customer_timeline(
    customer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.get_customer_timeline", {"customer_id": str(customer_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/{customer_id}/summary")
async def get_customer_summary(
    customer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "crm.generate_customer_summary",
            {"customer_id": str(customer_id)},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
