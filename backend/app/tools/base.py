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
    # Phase 4: populated only when actor_type == ActorType.AGENT. Carries
    # the exact Agent + AgentVersion this call is running under, so
    # ToolRegistry.execute() can run the two new agent-governance checks
    # (KLAROS_FINAL_AGENT_MODEL.md "Governance chain" steps 4-5) against
    # the immutable AgentVersion.tool_permissions_snapshot rather than the
    # live, mutable AgentToolPermission table — see registry.py's
    # `_check_agent_permission`/`_check_agent_autonomy`. Never set for any
    # other actor_type; a USER/AI/SYSTEM/WORKFLOW call with these set would
    # be a bug, not a supported pattern.
    agent_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    # Phase 7 (Agent Runtime Reliability II): a deterministic identity for
    # the logical tool operation this call represents — ALWAYS the same
    # string for the same logical operation (same AgentExecution +
    # AgentExecutionStep), across retries, crash recovery, and process
    # restarts. Never a fresh uuid4() per attempt (see
    # AgentExecutionService._idempotency_identity /
    # AgentReasoningService's equivalent). Server-generated only — never
    # settable by the LLM/model output, never read from tool output. NULL
    # for every non-agent actor and for any agent call this phase doesn't
    # yet thread it through; a tool MUST treat NULL as "no idempotency
    # identity available" and must never invent one itself. Purely
    # additive/optional: a tool that ignores this field behaves exactly as
    # it did before this phase.
    idempotency_key: str | None = None


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
    # Phase 7 (Agent Runtime Reliability II): an optional, explicit
    # capability indicator — False (the conservative default) for every
    # existing tool, so no tool needs to change to remain valid. Only set
    # True by a tool whose own implementation has a VERIFIED, tested
    # mechanism for making a retried call with the same
    # `ExecutionContext.idempotency_key` resolve to at most one logical
    # side effect (a provider's own idempotency-key header, a DB unique
    # constraint + upsert, or a naturally read-only/idempotent-by-nature
    # operation) — see PHASE_7_IMPLEMENTATION_LOG.md's per-tool review.
    # Consulted only by AgentExecutionService/AgentRecoveryService when
    # deciding whether an ambiguous post-crash outcome may be safely
    # retried (True) or must be safe-halted for human review (False, the
    # default — never a guess, never an overclaim).
    supports_idempotency: bool = False

    @abstractmethod
    async def execute(self, input: BaseModel, context: ExecutionContext) -> BaseModel: ...
