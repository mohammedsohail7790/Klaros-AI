"""Phase 4 (Agent Runtime): process-wide `AgentService`/`AgentExecutionService`
factories, mirroring app/api/tool_deps.py::get_automation_service's exact
`@lru_cache` + `Depends(get_tool_registry)` pattern — `AgentExecutionService`
needs the SAME ToolRegistry instance every other execution path uses (real
singleton in production, test-overridable instance in tests), never a
second one constructed ad hoc."""

from functools import lru_cache

from fastapi import Depends

from app.api.tool_deps import get_tool_registry
from app.db.session import async_session_maker
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_reasoning_service import AgentReasoningService
from app.services.agent_recovery_service import AgentRecoveryService
from app.services.agent_service import AgentService
from app.services.agent_trigger_service import AgentTriggerService
from app.tools.registry import ToolRegistry


@lru_cache
def get_agent_service() -> AgentService:
    return AgentService(async_session_maker)


@lru_cache
def get_agent_execution_service(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> AgentExecutionService:
    return AgentExecutionService(async_session_maker, tool_registry)


@lru_cache
def get_agent_reasoning_service(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> AgentReasoningService:
    # Phase 5: the SAME process-wide ToolRegistry singleton every other
    # execution path uses (see this module's own docstring) — never a
    # second one constructed ad hoc.
    return AgentReasoningService(
        async_session_maker, tool_registry, AgentExecutionService(async_session_maker, tool_registry)
    )


@lru_cache
def get_agent_recovery_service(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> AgentRecoveryService:
    """Phase 6: its own `AgentReasoningService` instance with its own
    `worker_id` — a recovery worker is conceptually a distinct process from
    whatever originally started an execution, even when (as in this
    single-process deployment) it happens to run in the same Python
    process; keeping the instances/worker_ids distinct is what makes the
    "does a recovery worker ever steal a still-alive owner's lease" test
    meaningful even in-process."""
    reasoning_service = AgentReasoningService(
        async_session_maker, tool_registry, AgentExecutionService(async_session_maker, tool_registry)
    )
    return AgentRecoveryService(async_session_maker, reasoning_service)


@lru_cache
def get_agent_trigger_service(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> AgentTriggerService:
    return AgentTriggerService(async_session_maker, get_agent_reasoning_service(tool_registry))
