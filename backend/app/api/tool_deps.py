from functools import lru_cache

from app.api.deps import CurrentUser
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.events.crm_handlers import register_crm_handlers
from app.events.factory import get_event_bus
from app.events.finance_handlers import register_finance_handlers
from app.events.handlers import register_default_handlers
from app.events.marketing_handlers import register_marketing_handlers
from app.events.retention_handlers import register_retention_handlers
from app.events.operations_handlers import register_operations_handlers
from app.events.notification_handlers import register_notification_handlers
from app.models.actor import ActorType
from app.tools.base import ExecutionContext
from app.tools.factory import build_tool_registry
from app.tools.registry import ToolRegistry


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return build_tool_registry(async_session_maker, get_event_bus())


@lru_cache
def get_morning_brief_service():
    """A second, lightweight MorningBriefService instance for the scheduler
    (see app/main.py's lifespan) — sharing the same session_factory and
    ToolRegistry singleton as get_tool_registry(). All actual state lives in
    Postgres, so a second instance is stateless and safe; it exists only
    because the scheduler runs with no human ExecutionContext to hand a Tool
    call, unlike the `insights.generate_morning_brief` tool a human triggers.
    """
    from app.ai.execution_service import AIExecutionService
    from app.services.morning_brief_service import MorningBriefService

    return MorningBriefService(async_session_maker, AIExecutionService(get_tool_registry()), get_wired_event_bus())


@lru_cache
def get_wired_event_bus() -> EventBus:
    bus = get_event_bus()
    register_default_handlers(bus)
    register_crm_handlers(bus, async_session_maker)
    register_operations_handlers(bus, async_session_maker)
    register_finance_handlers(bus, async_session_maker)
    register_marketing_handlers(bus, async_session_maker)
    register_retention_handlers(bus, async_session_maker)
    register_notification_handlers(bus, async_session_maker)
    return bus


def execution_context(current_user: CurrentUser) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=current_user.tenant_id,
        actor_type=ActorType.USER,
        actor_id=current_user.id,
        role=current_user.role,
    )


def raise_http_for_tool_error(exc: Exception) -> None:
    """Shared mapping from ToolRegistry exceptions to HTTP status codes, used
    by every CRM router so the mapping is defined once."""
    from fastapi import HTTPException, status

    from app.tools.errors import (
        ToolApprovalRequiredError,
        ToolBlockedError,
        ToolNotFoundError,
        ToolPermissionError,
        ToolValidationError,
    )

    if isinstance(exc, ToolNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ToolPermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ToolValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if isinstance(exc, ToolBlockedError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ToolApprovalRequiredError):
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={"status": "pending_approval", "approval_request_id": str(exc.approval_request_id)},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raise
