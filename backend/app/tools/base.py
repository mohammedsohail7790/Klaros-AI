"""Typed tool interface (section 4).

Every capability the AI can invoke — and, deliberately, everything a human
workflow or Temporal activity invokes too — is one of these. There is no path
from the AI to raw SQL, shell commands, or arbitrary HTTP: it can only ever
construct a `ToolInput` for a registered `Tool` and hand it to the
`ToolRegistry`, which enforces auth before `execute()` ever runs.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel

from app.models.actor import ActorType
from app.models.rbac import Permission, Role


@dataclass
class ExecutionContext:
    """Who is asking, and on whose behalf. Every tool call carries one."""

    tenant_id: uuid.UUID | None
    actor_type: ActorType
    actor_id: uuid.UUID | None
    role: Role | None
    correlation_id: uuid.UUID | None = None


class Tool(ABC):
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    required_permission: Permission | None = None
    tenant_scoped: bool = True
    # Counted against the tenant's plan's monthly AI-recommendation limit
    # (app/services/billing_service.py::PLAN_LIMITS) before execution.
    # False for every tool by default — only insights.generate_morning_brief
    # sets this, since it's the one feature the pricing page's "AI Next
    # Action" recommendation cap describes.
    counts_toward_ai_usage: bool = False

    @abstractmethod
    async def execute(self, input: BaseModel, context: ExecutionContext) -> BaseModel: ...
