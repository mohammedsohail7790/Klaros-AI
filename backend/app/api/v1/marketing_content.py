import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.marketing import ContentVariant, MarketingContent
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/marketing/content", tags=["marketing-content"])


def _content_to_dict(c: MarketingContent) -> dict[str, Any]:
    return {
        "id": str(c.id), "source_job_id": str(c.source_job_id) if c.source_job_id else None, "title": c.title,
        "summary": c.summary, "status": c.status, "ai_generated": c.ai_generated,
    }


@router.get("")
async def list_content(
    status: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    query = select(MarketingContent).where(MarketingContent.tenant_id == current_user.tenant_id)
    if status:
        query = query.where(MarketingContent.status == status)
    rows = (await db.execute(query.order_by(MarketingContent.created_at.desc()))).scalars().all()
    return {"content": [_content_to_dict(c) for c in rows]}


@router.get("/{content_id}")
async def get_content(
    content_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    content = await db.get(MarketingContent, content_id)
    if content is None or content.tenant_id != current_user.tenant_id:
        from fastapi import HTTPException, status as http_status

        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Content not found")
    variants = (
        await db.execute(
            select(ContentVariant).where(ContentVariant.tenant_id == current_user.tenant_id, ContentVariant.content_id == content_id)
        )
    ).scalars().all()
    result = _content_to_dict(content)
    result["variants"] = [
        {"id": str(v.id), "channel": v.channel, "body_text": v.body_text, "status": v.status} for v in variants
    ]
    return result


@router.post("/ideas")
async def create_idea(
    body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.create_content_idea", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/generate-from-job")
async def generate_from_job(
    job_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.generate_content_draft_from_job", {"job_id": str(job_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{content_id}/variants")
async def add_variant(
    content_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {channel, body_text?}"""
    payload = {"content_id": str(content_id), **body}
    try:
        output = await registry.execute("marketing.add_content_variant", payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{content_id}/request-approval")
async def request_approval(
    content_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.request_content_approval", {"content_id": str(content_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{content_id}/approve")
async def approve_content(
    content_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.approve_content", {"content_id": str(content_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/{content_id}/reject")
async def reject_content(
    content_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute("marketing.reject_content", {"content_id": str(content_id)}, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/variants/{content_variant_id}/publish")
async def publish_variant(
    content_variant_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.publish_content_variant", {"content_variant_id": str(content_variant_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
