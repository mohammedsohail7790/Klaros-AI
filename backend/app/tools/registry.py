"""section 5/6: the tool registry.

This is the single choke point every tool call passes through — human,
workflow, or AI. `execute()` is the enforcement pipeline from the spec:

    lookup -> permission check -> tenant check -> schema validation
    -> policy check (AUTO / APPROVAL_REQUIRED / BLOCKED) -> execute -> audit

A caller (including the AI execution service in app/ai/execution_service.py)
has no other way to run a tool.
"""

import uuid

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.rbac import role_has_permission
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import (
    ToolApprovalRequiredError,
    ToolBillingLimitError,
    ToolBlockedError,
    ToolKillSwitchError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolValidationError,
)
from app.tools.policy import ActionPolicy
from app.tools.redact import redact_input

logger = structlog.get_logger(__name__)

# Fallback entity inference from raw tool input, when the entity can't be
# derived from the tool's output (e.g. a failed call, or a read/search tool
# with no single-entity output). Order matters — most specific first.
_INPUT_ENTITY_ID_FIELDS: list[str] = [
    "job_id", "customer_id", "lead_id", "appointment_id", "task_id",
    "exception_id", "scope_change_id", "worker_id", "purchase_order_id",
    "event_id",
]


def _infer_entity(raw_input: dict, output=None) -> tuple[str | None, uuid.UUID | None]:
    """Best-effort (entity_type, entity_id) for an audit row, so job/customer
    timelines (which filter AuditLog by entity_type/entity_id) actually find
    the tool calls that acted on them — including ones like `create_job`
    where the entity doesn't exist until the call succeeds, so it can only
    come from the output, not the input.
    """
    if output is not None:
        try:
            dumped = output.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            dumped = None
        if isinstance(dumped, dict):
            for key, value in dumped.items():
                if isinstance(value, dict) and isinstance(value.get("id"), str):
                    try:
                        return key, uuid.UUID(value["id"])
                    except ValueError:
                        continue

    for field in _INPUT_ENTITY_ID_FIELDS:
        value = raw_input.get(field)
        if isinstance(value, str):
            try:
                return field.removesuffix("_id"), uuid.UUID(value)
            except ValueError:
                continue

    return None, None


class ToolRegistry:
    def __init__(
        self, session_factory: async_sessionmaker, bus=None, policy_service=None, billing_service=None
    ) -> None:
        self._session_factory = session_factory
        self._tools: dict[str, Tool] = {}
        # Optional (Phase 9): lets the registry publish `approval.requested`
        # when it creates an ApprovalRequest itself. None is fine — nothing
        # else in the registry depends on it — but every real deployment
        # wires it via app/tools/factory.py so the event actually fires.
        self._bus = bus
        # Phase 10A: resolves the tenant-effective policy for a tool at
        # execution time. Defaults to a real PolicyService against the same
        # session_factory if the caller doesn't supply one — every real
        # deployment gets tenant-configurable policy without every call site
        # needing to know about it.
        if policy_service is None:
            from app.services.policy_service import PolicyService

            policy_service = PolicyService(session_factory)
        self._policy_service = policy_service
        # Same lazy-default pattern as policy_service above — every real
        # deployment gets plan/usage gating on counts_toward_ai_usage tools
        # without every call site needing to know about it.
        if billing_service is None:
            from app.services.billing_service import BillingService

            billing_service = BillingService(session_factory)
        self._billing_service = billing_service

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Unknown tool: {name}")
        return tool

    def list_available(self, context: ExecutionContext) -> list[Tool]:
        return [
            t
            for t in self._tools.values()
            if t.required_permission is None
            or (context.role is not None and role_has_permission(context.role, t.required_permission))
        ]

    async def execute(
        self, name: str, raw_input: dict, context: ExecutionContext, *, skip_approval_gate: bool = False
    ):
        """`skip_approval_gate` is set ONLY by `ApprovalExecutionService` when
        resuming a tool call that has already been through and passed the
        approval boundary — never by any other caller (human, AI, workflow).
        Every other check (permission, tenant, schema, BLOCKED policy) still
        runs; only the APPROVAL_REQUIRED branch below is skipped, so a
        second approval request is never created for an already-approved
        action, and — just as importantly — a policy that has since changed
        to BLOCKED still stops execution even on resume.
        """
        tool = self.get(name)

        if context.actor_type == ActorType.AGENT:
            # Phase 4 (KLAROS_FINAL_AGENT_MODEL.md "Governance chain"
            # steps 4-5): two new pre-checks, inserted before every
            # existing check below (including the kill switch and RBAC
            # checks that already run) — never replacing or reordering
            # them. Both can only NARROW what would otherwise be allowed;
            # neither can widen it.
            await self._check_agent_tool_permission(name, context)
            policy_ceiling = await self._check_agent_autonomy(name, context)
        else:
            policy_ceiling = None

        if context.actor_type != ActorType.USER and context.tenant_id is not None:
            org = await self._get_organization(context.tenant_id)
            if org is not None and org.ai_paused:
                await self._audit(
                    context, tool_name=name, raw_input=raw_input, result="failure", error="ai_kill_switch_active"
                )
                raise ToolKillSwitchError(
                    "This tenant's AI kill switch is on — AI and automation actions are paused. "
                    "A human can still act directly; turn the switch off in Settings to resume."
                )

        if tool.required_permission is not None:
            if context.role is None or not role_has_permission(context.role, tool.required_permission):
                await self._audit(
                    context,
                    tool_name=name,
                    raw_input=raw_input,
                    result="failure",
                    error="permission_denied",
                )
                raise ToolPermissionError(
                    f"Actor lacks required permission: {tool.required_permission.value}"
                )

        if tool.tenant_scoped and context.tenant_id is None:
            # Can't write a tenant-scoped audit row with no tenant — log and reject.
            logger.warning("tool_execution_rejected_missing_tenant", tool=name)
            raise ToolPermissionError("Tool requires a tenant-scoped execution context")

        if tool.counts_toward_ai_usage and context.tenant_id is not None:
            org = await self._get_organization(context.tenant_id)
            if org is not None:
                try:
                    await self._billing_service.check_ai_usage_allowed(org)
                except ToolBillingLimitError as exc:
                    await self._audit(
                        context, tool_name=name, raw_input=raw_input, result="failure", error=str(exc)
                    )
                    raise

        try:
            validated_input = tool.input_schema.model_validate(raw_input)
        except ValidationError as exc:
            await self._audit(
                context, tool_name=name, raw_input=raw_input, result="failure", error=str(exc)
            )
            raise ToolValidationError(str(exc)) from exc

        policy = await self._policy_service.resolve(context.tenant_id, name)

        if policy_ceiling is not None:
            # Stricter of the tool's own resolved policy and the agent's
            # autonomy-tier ceiling wins — composition can only narrow,
            # never widen (KLAROS_FINAL_AGENT_MODEL.md). Strictness order:
            # BLOCKED > APPROVAL_REQUIRED > AUTO.
            _STRICTNESS = {ActionPolicy.AUTO: 0, ActionPolicy.APPROVAL_REQUIRED: 1, ActionPolicy.BLOCKED: 2}
            if _STRICTNESS[policy_ceiling] > _STRICTNESS[policy]:
                policy = policy_ceiling

        if policy == ActionPolicy.BLOCKED:
            await self._audit(
                context, tool_name=name, raw_input=raw_input, result="failure", error="blocked_by_policy"
            )
            raise ToolBlockedError(f"Tool '{name}' is blocked by policy")

        if policy == ActionPolicy.APPROVAL_REQUIRED and not skip_approval_gate:
            request_id = await self._create_approval_request(context, tool, validated_input)
            await self._audit(
                context,
                tool_name=name,
                raw_input=raw_input,
                result="pending_approval",
                approval_id=request_id,
            )
            raise ToolApprovalRequiredError(request_id)

        try:
            output = await tool.execute(validated_input, context)
        except Exception as exc:  # noqa: BLE001 — always audited, never swallowed
            await self._audit(
                context, tool_name=name, raw_input=raw_input, result="failure", error=str(exc)
            )
            raise

        entity_type, entity_id = _infer_entity(raw_input, output)
        await self._audit(
            context,
            tool_name=name,
            raw_input=raw_input,
            result="success",
            entity_type=entity_type,
            entity_id=entity_id,
        )
        return output

    async def _create_approval_request(
        self, context: ExecutionContext, tool: Tool, validated_input
    ) -> uuid.UUID:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            request = ApprovalRequest(
                tenant_id=context.tenant_id,
                requested_by_type=context.actor_type,
                requested_by_id=context.actor_id,
                requested_by_role=context.role.value if context.role else None,
                tool_name=tool.name,
                action_type=tool.name.split(".")[0],
                reason=f"Tool '{tool.name}' requires approval by policy",
                tool_input=validated_input.model_dump(mode="json"),
                status=ApprovalStatus.PENDING,
                correlation_id=context.correlation_id,
                idempotency_key=f"approval-exec-{context.correlation_id or uuid.uuid4()}",
                # Phase 4: carried through so ApprovalExecutionService can
                # reconstruct the agent-governed ExecutionContext on resume
                # (see approval.py's Phase 4 column comment) and so the
                # originating AgentExecution can be found and updated
                # without a second lookup mechanism.
                agent_id=context.agent_id,
                agent_version_id=context.agent_version_id,
                agent_execution_id=context.correlation_id if context.actor_type == ActorType.AGENT else None,
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)
            request_id = request.id

        if self._bus is not None:
            from app.models.event import EventType

            await self._bus.publish(
                tenant_id=context.tenant_id,
                event_type=EventType.APPROVAL_REQUESTED,
                source="tool_registry",
                entity_type="approval_request",
                entity_id=request_id,
                payload={"tool_name": tool.name},
                correlation_id=context.correlation_id,
            )
        return request_id

    async def _check_agent_tool_permission(self, tool_name: str, context: ExecutionContext) -> None:
        """KLAROS_FINAL_AGENT_MODEL.md governance chain step 4. Checks the
        immutable `AgentVersion.tool_permissions_snapshot` for the exact
        version this call is running under — never the live
        `AgentToolPermission` table — so a published version's authorized
        tool set can never silently change after the fact (Version
        Immutability). Deny-by-default: no `agent_version_id` context, no
        snapshot, or the tool simply absent from it, all reject."""
        from app.models.agent import Agent, AgentStatus, AgentVersion, AgentVersionStatus

        if context.agent_id is None or context.agent_version_id is None:
            raise ToolPermissionError("Agent-actor tool call missing agent identity")

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            agent = await session.get(Agent, context.agent_id)
            if agent is None or agent.tenant_id != context.tenant_id:
                raise ToolPermissionError("Unknown agent")
            if agent.status != AgentStatus.ACTIVE:
                raise ToolPermissionError(f"Agent is not ACTIVE (status={agent.status})")

            version = await session.get(AgentVersion, context.agent_version_id)
            if version is None or version.tenant_id != context.tenant_id or version.agent_id != agent.id:
                raise ToolPermissionError("Unknown agent version")
            if version.status != AgentVersionStatus.PUBLISHED:
                raise ToolPermissionError(f"Agent version is not PUBLISHED (status={version.status})")
            if agent.current_version_id != version.id:
                raise ToolPermissionError("Only the agent's current active version may execute")

        granted = {entry.get("tool_name") for entry in (version.tool_permissions_snapshot or [])}
        if tool_name not in granted:
            raise ToolPermissionError(
                f"Agent '{agent.id}' is not granted tool '{tool_name}' (deny-by-default)"
            )

    async def _check_agent_autonomy(self, tool_name: str, context: ExecutionContext) -> ActionPolicy | None:
        """KLAROS_FINAL_AGENT_MODEL.md governance chain step 5. Returns the
        autonomy-tier's policy CEILING for this call — composed with (never
        replacing) the tool's own resolved ActionPolicy in `execute()`
        above, always taking whichever is stricter. See
        app/models/agent.py::AgentAutonomyTier's docstring for the exact
        per-tier semantics this implements."""
        from app.models.agent import Agent, AgentAutonomyTier

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            agent = await session.get(Agent, context.agent_id)
        if agent is None:
            raise ToolPermissionError("Unknown agent")

        tier = agent.autonomy_tier
        if tier in (AgentAutonomyTier.OBSERVE, AgentAutonomyTier.RECOMMEND):
            return ActionPolicy.BLOCKED
        if tier == AgentAutonomyTier.EXECUTE_WITH_APPROVAL:
            return ActionPolicy.APPROVAL_REQUIRED
        return ActionPolicy.AUTO

    async def _get_organization(self, tenant_id: uuid.UUID):
        from app.models.organization import Organization

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return await session.get(Organization, tenant_id)

    async def _audit(
        self,
        context: ExecutionContext,
        *,
        tool_name: str,
        raw_input: dict,
        result: str,
        error: str | None = None,
        approval_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
    ) -> None:
        summary = redact_input(raw_input)
        if error:
            summary = {**summary, "_error": error}

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            session.add(
                AuditLog(
                    tenant_id=context.tenant_id,
                    actor_type=context.actor_type,
                    actor_id=context.actor_id,
                    action=f"tool.execute:{tool_name}",
                    tool=tool_name,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    input_summary=summary,
                    result=result,
                    approval_id=approval_id,
                    correlation_id=context.correlation_id,
                )
            )
            await session.commit()

        log = logger.info if result == "success" else logger.warning
        log(
            "tool_execution",
            tool=tool_name,
            actor_type=context.actor_type,
            tenant_id=str(context.tenant_id) if context.tenant_id else None,
            result=result,
        )
