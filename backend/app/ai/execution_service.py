"""section 7: the AI execution boundary.

This is the *only* surface the AI orchestration layer (Phase 7) is allowed to
call. It takes a structured tool request and hands it to the ToolRegistry —
nothing else. There is deliberately no method here that accepts a SQL
string, a shell command, an HTTP URL, or a Python callable: the only thing
an AIExecutionService can do is `request_tool_execution`.

    AI -> decision -> ToolRequest -> AIExecutionService -> ToolRegistry
                                                                |
                                                     permission/tenant/schema/
                                                     policy checks + audit

Phase 2 does not build the autonomous agent that produces ToolRequests — see
PROJECT_STATUS.md. This module only establishes the boundary that agent will
be required to call through.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.registry import ToolRegistry


@dataclass
class ToolRequest:
    tool_name: str
    input: dict[str, Any]


class AIExecutionService:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def request_tool_execution(
        self,
        request: ToolRequest,
        *,
        tenant_id: uuid.UUID,
        ai_role: Role,
        correlation_id: uuid.UUID | None = None,
    ):
        context = ExecutionContext(
            tenant_id=tenant_id,
            actor_type=ActorType.AI,
            actor_id=None,
            role=ai_role,
            correlation_id=correlation_id,
        )
        return await self._registry.execute(request.tool_name, request.input, context)
