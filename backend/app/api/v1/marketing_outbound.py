import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.marketing import OutboundContact, OutboundEnrollment, OutboundList, OutboundSequence
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/outbound", tags=["marketing-outbound"])


@router.get("/lists")
async def list_lists(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(OutboundList).where(OutboundList.tenant_id == current_user.tenant_id))).scalars().all()
    return {"lists": [{"id": str(l.id), "name": l.name, "description": l.description} for l in rows]}


@router.post("/lists")
async def create_list(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_outbound_list", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/contacts")
async def list_contacts(list_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    query = select(OutboundContact).where(OutboundContact.tenant_id == current_user.tenant_id)
    if list_id:
        query = query.where(OutboundContact.list_id == list_id)
    rows = (await db.execute(query)).scalars().all()
    return {
        "contacts": [
            {
                "id": str(c.id), "list_id": str(c.list_id), "company": c.company, "contact_name": c.contact_name,
                "email": c.email, "phone": c.phone, "source": c.source, "enrichment_status": c.enrichment_status,
                "qualification_status": c.qualification_status,
            }
            for c in rows
        ]
    }


@router.post("/contacts")
async def add_contact(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.add_outbound_contact", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/sequences")
async def list_sequences(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(OutboundSequence).where(OutboundSequence.tenant_id == current_user.tenant_id))).scalars().all()
    return {"sequences": [{"id": str(s.id), "name": s.name, "status": s.status} for s in rows]}


@router.post("/sequences")
async def create_sequence(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_outbound_sequence", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/sequences/{sequence_id}/steps")
async def add_step(sequence_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    payload = {"sequence_id": str(sequence_id), **body}
    try:
        output = await registry.execute("marketing.add_outbound_step", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/enrollments")
async def enroll_contact(body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    """body = {sequence_id, contact_id}"""
    try:
        output = await registry.execute("marketing.enroll_outbound_contact", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/enrollments")
async def list_enrollments(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(OutboundEnrollment).where(OutboundEnrollment.tenant_id == current_user.tenant_id))).scalars().all()
    return {
        "enrollments": [
            {"id": str(e.id), "sequence_id": str(e.sequence_id), "contact_id": str(e.contact_id), "status": e.status}
            for e in rows
        ]
    }


@router.post("/activities/execute-due")
async def execute_due(current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.execute_due_outbound_activities", {}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
