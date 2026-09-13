"""Phase 25: this module is the single application-container boundary for
every runtime path (HTTP requests, the in-process EventWorker, the
standalone worker OS process, and tests) — the ONE place that constructs
the application's ToolRegistry and wired EventBus.

Root cause this phase fixes: `get_automation_service`/
`get_morning_brief_service`/`get_voice_conversation_service` used to call
`get_tool_registry()`/`get_wired_event_bus()` as bare Python function
calls rather than accepting them as FastAPI-resolved parameters. A bare
call to an `@lru_cache`d function is invisible to
`app.dependency_overrides` — only `Depends(fn)` resolution consults the
override table — so any route depending on one of those three services
silently fell through to the real process-wide singleton instead of a
test's isolated EventBus/ToolRegistry, even when the test had correctly
overridden `get_tool_registry`/`get_wired_event_bus` for its own request.
This is exactly the dual-EventBus behavior Phase 24's real-HTTP test had
to explicitly work around for `POST /automations/scheduled/dispatch-tick`.

The fix: every function below that needs the ToolRegistry or the wired
EventBus now takes it as a `Depends(...)`-defaulted parameter, so FastAPI
threads the SAME resolved (and, in tests, override-able) instance through
the whole call graph. `get_tool_registry` and `get_wired_event_bus`
themselves remain bare, cached factory functions — they are the two
necessary bootstrap roots (a ToolRegistry needs a bare EventBus to let
tools publish; the wired EventBus needs a ToolRegistry to build automation
handlers that execute tools), and every other consumer sits downstream of
them through real dependency injection, never a second raw call.

Non-request callers (app/main.py's lifespan, app/events/worker.py's
standalone process) are NOT inside FastAPI's request/DI scope, so they
call these functions directly — but must now pass the bootstrap
instances explicitly (`get_automation_service(get_tool_registry())`)
rather than relying on the old zero-arg raw-call form. Both callers were
updated alongside this file.
"""

from functools import lru_cache

from fastapi import Depends

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
def get_wired_event_bus() -> EventBus:
    from app.ai.execution_service import AIExecutionService
    from app.events.automation_handlers import register_automation_handlers

    bus = get_event_bus()
    register_default_handlers(bus)
    register_crm_handlers(bus, async_session_maker)
    register_operations_handlers(bus, async_session_maker)
    register_finance_handlers(bus, async_session_maker)
    register_marketing_handlers(bus, async_session_maker)
    register_retention_handlers(bus, async_session_maker)
    register_notification_handlers(bus, async_session_maker)
    register_automation_handlers(bus, async_session_maker, AIExecutionService(get_tool_registry()))
    return bus


@lru_cache
def get_morning_brief_service(
    tool_registry: ToolRegistry = Depends(get_tool_registry), bus: EventBus = Depends(get_wired_event_bus),
):
    """A second, lightweight MorningBriefService instance for the scheduler
    (see app/main.py's lifespan) — sharing the same session_factory and
    ToolRegistry singleton as get_tool_registry(). All actual state lives in
    Postgres, so a second instance is stateless and safe; it exists only
    because the scheduler runs with no human ExecutionContext to hand a Tool
    call, unlike the `insights.generate_morning_brief` tool a human triggers.
    `@lru_cache` still holds: in production `tool_registry`/`bus` are always
    the same two singleton objects, so this resolves to one cached instance;
    in tests, each test's distinct ToolRegistry/EventBus objects produce
    their own cache entry, so nothing leaks between tests.
    """
    from app.ai.execution_service import AIExecutionService
    from app.services.morning_brief_service import MorningBriefService

    return MorningBriefService(async_session_maker, AIExecutionService(tool_registry), bus)


@lru_cache
def get_voice_call_service():
    from app.services.voice_call_service import VoiceCallService

    return VoiceCallService(async_session_maker)


@lru_cache
def get_voice_conversation_service(tool_registry: ToolRegistry = Depends(get_tool_registry)):
    from app.ai.execution_service import AIExecutionService
    from app.services.ai_provider import get_ai_provider
    from app.services.knowledge_qa_service import KnowledgeQAService
    from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
    from app.services.knowledge_service import KnowledgeService
    from app.services.voice_conversation_service import VoiceConversationService

    knowledge_service = KnowledgeService(async_session_maker)
    retrieval_service = KnowledgeRetrievalService(async_session_maker, knowledge_service)
    qa_service = KnowledgeQAService(async_session_maker, retrieval_service, get_ai_provider())
    return VoiceConversationService(
        async_session_maker, get_voice_call_service(), AIExecutionService(tool_registry),
        get_ai_provider(), qa_service,
    )


def get_openai_realtime_voice_bridge(tool_registry: ToolRegistry):
    """Phase 32: deliberately NOT @lru_cache'd, unlike the singletons
    above — an OpenAIRealtimeVoiceBridge holds live, per-call state (the
    WebSocket, the slot/customer guard) and must be a fresh instance for
    every call, never shared/reused. Called directly (not via
    `Depends(...)`) from app/api/v1/voice_stream.py's WebSocket route,
    matching how `get_voice_conversation_service(get_tool_registry())`
    is already called there — WebSocket routes sit outside FastAPI's
    normal Depends-injection scope, same rationale as this module's
    docstring already explains for non-request callers."""
    from app.ai.execution_service import AIExecutionService
    from app.services.openai_realtime_voice_service import OpenAIRealtimeVoiceBridge

    return OpenAIRealtimeVoiceBridge(
        tool_registry, AIExecutionService(tool_registry), async_session_maker, get_voice_call_service(),
    )


@lru_cache
def get_automation_service(tool_registry: ToolRegistry = Depends(get_tool_registry)):
    from app.ai.execution_service import AIExecutionService
    from app.services.automation_service import AutomationService

    return AutomationService(async_session_maker, AIExecutionService(tool_registry))


@lru_cache
def get_company_memory_service():
    from app.services.company_memory_service import CompanyMemoryService

    return CompanyMemoryService(async_session_maker)


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
        ToolError,
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
    # Base ToolError (e.g. "not connected" / "external request failed" from
    # the Google Calendar, QuickBooks, and Stripe tools) has no subclass of
    # its own here and would otherwise fall through to the bare `raise`
    # below and crash as an unhandled 500 — confirmed live via
    # calendar.list_google_calendars for a tenant with no Google Calendar
    # connection. It's a client-actionable condition, not a server bug.
    if isinstance(exc, ToolError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise
