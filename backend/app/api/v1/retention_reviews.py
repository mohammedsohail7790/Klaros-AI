import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.models.retention import CustomerFeedback, ReviewRequest
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/retention/reviews", tags=["retention-reviews"])


def _review_to_dict(r: ReviewRequest) -> dict[str, Any]:
    return {
        "id": str(r.id), "customer_id": str(r.customer_id), "job_id": str(r.job_id) if r.job_id else None,
        "channel": r.channel, "status": r.status, "requested_at": r.requested_at.isoformat() if r.requested_at else None,
    }


def _feedback_to_dict(f: CustomerFeedback) -> dict[str, Any]:
    return {
        "id": str(f.id), "customer_id": str(f.customer_id), "job_id": str(f.job_id) if f.job_id else None,
        "rating": f.rating, "sentiment": f.sentiment, "comment": f.comment, "received_at": f.received_at.isoformat(),
        "consent_to_use_publicly": f.consent_to_use_publicly,
    }


@router.get("/requests")
async def list_review_requests(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (
        await db.execute(select(ReviewRequest).where(ReviewRequest.tenant_id == current_user.tenant_id).order_by(ReviewRequest.created_at.desc()))
    ).scalars().all()
    return {"review_requests": [_review_to_dict(r) for r in rows]}


@router.post("/requests/{review_request_id}/send")
async def send_review_request(
    review_request_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "retention.send_review_request", {"review_request_id": str(review_request_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.get("/feedback")
async def list_feedback(
    sentiment: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    query = select(CustomerFeedback).where(CustomerFeedback.tenant_id == current_user.tenant_id)
    if sentiment:
        query = query.where(CustomerFeedback.sentiment == sentiment)
    rows = (await db.execute(query.order_by(CustomerFeedback.received_at.desc()))).scalars().all()
    return {"feedback": [_feedback_to_dict(f) for f in rows]}


@router.post("/feedback")
async def record_feedback(
    body: dict, current_user: CurrentUser = Depends(get_current_user), registry: ToolRegistry = Depends(get_tool_registry)
) -> dict[str, Any]:
    """body = {customer_id, job_id?, rating?, comment?, source?}"""
    try:
        output = await registry.execute("retention.record_feedback", body, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/feedback/{feedback_id}/consent")
async def record_review_consent(
    feedback_id: uuid.UUID, body: dict, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """body = {consent: bool} — a human recording a fact confirmed with the
    customer directly; see retention.record_review_consent's own docstring
    for why this can never be AI-callable."""
    try:
        output = await registry.execute(
            "retention.record_review_consent",
            {"feedback_id": str(feedback_id), "consent": body.get("consent")},
            execution_context(current_user),
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


@router.post("/feedback/{feedback_id}/create-content")
async def create_content_from_review(
    feedback_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    try:
        output = await registry.execute(
            "marketing.create_content_from_review", {"feedback_id": str(feedback_id)}, execution_context(current_user)
        )
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")
