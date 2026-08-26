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

from app.models.actor import ActorType
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.rbac import role_has_permission
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import (
    ToolApprovalRequiredError,
    ToolBlockedError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolValidationError,
)
from app.tools.policy import ActionPolicy, policy_for
from app.tools.redact import redact_input

logger = structlog.get_logger(__name__)


class ToolRegistry:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._tools: dict[str, Tool] = {}

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

    async def execute(self, name: str, raw_input: dict, context: ExecutionContext):
        tool = self.get(name)

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

        try:
            validated_input = tool.input_schema.model_validate(raw_input)
        except ValidationError as exc:
            await self._audit(
                context, tool_name=name, raw_input=raw_input, result="failure", error=str(exc)
            )
            raise ToolValidationError(str(exc)) from exc

        policy = policy_for(name)

        if policy == ActionPolicy.BLOCKED:
            await self._audit(
                context, tool_name=name, raw_input=raw_input, result="failure", error="blocked_by_policy"
            )
            raise ToolBlockedError(f"Tool '{name}' is blocked by policy")

        if policy == ActionPolicy.APPROVAL_REQUIRED:
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

        await self._audit(context, tool_name=name, raw_input=raw_input, result="success")
        return output

    async def _create_approval_request(
        self, context: ExecutionContext, tool: Tool, validated_input
    ) -> uuid.UUID:
        async with self._session_factory() as session:
            request = ApprovalRequest(
                tenant_id=context.tenant_id,
                requested_by_type=context.actor_type,
                requested_by_id=context.actor_id,
                tool_name=tool.name,
                action_type=tool.name.split(".")[0],
                reason=f"Tool '{tool.name}' requires approval by policy",
                tool_input=validated_input.model_dump(mode="json"),
                status=ApprovalStatus.PENDING,
                correlation_id=context.correlation_id,
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)
            return request.id

    async def _audit(
        self,
        context: ExecutionContext,
        *,
        tool_name: str,
        raw_input: dict,
        result: str,
        error: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> None:
        summary = redact_input(raw_input)
        if error:
            summary = {**summary, "_error": error}

        async with self._session_factory() as session:
            session.add(
                AuditLog(
                    tenant_id=context.tenant_id,
                    actor_type=context.actor_type,
                    actor_id=context.actor_id,
                    action=f"tool.execute:{tool_name}",
                    tool=tool_name,
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
