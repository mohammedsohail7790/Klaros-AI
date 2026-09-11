"""Phase 26: the Owner Operating System's single new real endpoint — the
Attention Queue. Every other owner-cockpit data source (CRM metrics,
commercial pipeline, operations, finance, marketing, retention, autonomy,
automations, approvals, Company Memory) already exists across
`crm.py`/`operations.py`/`finance.py`/`marketing.py`/`retention.py`/
`automation.py`/`approvals.py`/`company_memory.py` and is reused as-is —
this router does not duplicate any of them.

Read-only, tenant-scoped via the same `current_user.tenant_id` every
other authenticated endpoint uses — never a client-supplied tenant id.
Available to every authenticated role (OWNER/MANAGER/READ_ONLY/
TECHNICIAN); it is a view, not a mutation, so no extra permission gate is
needed beyond being a member of the tenant.
"""

from datetime import datetime, timedelta, timezone

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.models.ai_invocation import AIInvocationLog
from app.services.owner_activity_service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    OwnerActivityService,
)
from app.services.owner_attention_service import OwnerAttentionService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class AttentionItemOut(BaseModel):
    category: str
    priority: str
    score: int
    title: str
    reason: str
    entity_type: str
    entity_id: str
    link: str
    age_days: int | None = None
    monetary_value: str | None = None


class AttentionQueueResponse(BaseModel):
    items: list[AttentionItemOut]
    critical_count: int
    high_count: int


@router.get("/attention", response_model=AttentionQueueResponse)
async def get_attention_queue(current_user: CurrentUser = Depends(get_current_user)) -> AttentionQueueResponse:
    service = OwnerAttentionService(async_session_maker)
    items = await service.get_attention_queue(current_user.tenant_id)
    return AttentionQueueResponse(
        items=[AttentionItemOut(**i.to_dict()) for i in items],
        critical_count=sum(1 for i in items if i.priority == "CRITICAL"),
        high_count=sum(1 for i in items if i.priority == "HIGH"),
    )


class AIHealthResponse(BaseModel):
    provider_configured: bool
    provider_name: str
    invocations_24h: int
    invocations_24h_succeeded: int
    invocations_24h_failed: int


@router.get("/ai-health", response_model=AIHealthResponse)
async def get_ai_health(current_user: CurrentUser = Depends(get_current_user)) -> AIHealthResponse:
    """Step 10 (AI HEALTH): a cheap, real, honest signal — `is_connected`
    is a plain attribute on the resolved provider (no network call, no
    fabricated 'live' claim), and the 24h counts come straight from the
    same `AIInvocationLog` table the real /ai-activity page already reads.
    No second AI health tracking system."""
    from app.services.ai_provider import get_ai_provider

    provider = get_ai_provider()
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    async with async_session_maker() as session:
        total = (
            await session.execute(
                select(func.count()).where(
                    AIInvocationLog.tenant_id == current_user.tenant_id, AIInvocationLog.created_at >= since,
                )
            )
        ).scalar_one()
        succeeded = (
            await session.execute(
                select(func.count()).where(
                    AIInvocationLog.tenant_id == current_user.tenant_id, AIInvocationLog.created_at >= since,
                    AIInvocationLog.success.is_(True),
                )
            )
        ).scalar_one()
    return AIHealthResponse(
        provider_configured=provider.is_connected, provider_name=provider.name,
        invocations_24h=total, invocations_24h_succeeded=succeeded, invocations_24h_failed=total - succeeded,
    )


class ActivityItemOut(BaseModel):
    id: str
    timestamp: str
    activity_type: str
    category: str
    title: str
    description: str
    severity: str
    actor_type: str
    actor_name: str | None
    entity_type: str | None
    entity_id: str | None
    entity_label: str | None
    link: str | None
    status: str | None
    metadata: dict[str, Any] | None = None


class ActivityFeedResponse(BaseModel):
    items: list[ActivityItemOut]
    total: int
    page: int
    page_size: int


@router.get("/activity", response_model=ActivityFeedResponse)
async def get_activity_feed(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    category: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActivityFeedResponse:
    """Phase 27: the Owner Activity Feed — a read-only, human-readable
    projection of business history, aggregated from existing persisted
    sources by `OwnerActivityService` (see that module's own docstring for
    the full source-of-truth matrix). Never a new event bus/store/audit
    system; never a mutation. `tenant_id` always comes from
    `current_user.tenant_id`, never a query parameter — there is no way
    to pass a tenant id to this endpoint at all, by design."""
    service = OwnerActivityService(async_session_maker)
    items, total = await service.get_activity(
        current_user.tenant_id, page=page, page_size=page_size, category=category,
    )
    return ActivityFeedResponse(
        items=[ActivityItemOut(**i.to_dict()) for i in items], total=total, page=page, page_size=page_size,
    )
