"""Phase 10A: tools wrapping PolicyService — the frontend /settings/automation
page and API only ever go through these, same pattern as every other domain.
AUTO at the policy layer, permission-gated instead (same reasoning as
Phase 9's approvals.* tools: a second "approval required to change a
policy" dead end would be silly)."""

from pydantic import BaseModel

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.rbac import Permission
from app.services.policy_service import PolicyService
from app.tools.base import ExecutionContext, Tool


class ListPoliciesInput(BaseModel):
    pass


class PolicyRow(BaseModel):
    tool_name: str
    default_policy: str
    current_policy: str
    has_override: bool
    system_blocked: bool
    configured_by: str | None
    updated_at: str | None
    version: int


class ListPoliciesOutput(BaseModel):
    policies: list[PolicyRow]


class ListPolicies(Tool):
    name = "automation.list_policies"
    description = "List every tool's default and tenant-effective automation policy."
    input_schema = ListPoliciesInput
    output_schema = ListPoliciesOutput
    required_permission = Permission.READ_AUTOMATION_POLICIES

    def __init__(self, policy_service: PolicyService) -> None:
        self._policy_service = policy_service

    async def execute(self, input: ListPoliciesInput, context: ExecutionContext) -> ListPoliciesOutput:
        rows = await self._policy_service.list_policies(context.tenant_id)
        return ListPoliciesOutput(policies=[PolicyRow(**r) for r in rows])


class GetPolicyInput(BaseModel):
    tool_name: str


class GetPolicy(Tool):
    name = "automation.get_policy"
    description = "Get one tool's default and tenant-effective automation policy."
    input_schema = GetPolicyInput
    output_schema = PolicyRow
    required_permission = Permission.READ_AUTOMATION_POLICIES

    def __init__(self, policy_service: PolicyService) -> None:
        self._policy_service = policy_service

    async def execute(self, input: GetPolicyInput, context: ExecutionContext) -> PolicyRow:
        detail = await self._policy_service.get_policy_detail(context.tenant_id, input.tool_name)
        return PolicyRow(**detail)


class SetPolicyInput(BaseModel):
    tool_name: str
    policy: str


class SetPolicy(Tool):
    name = "automation.set_policy"
    description = "Set a tenant-specific override for one tool's automation policy."
    input_schema = SetPolicyInput
    output_schema = PolicyRow
    required_permission = Permission.MANAGE_AUTOMATION_POLICIES

    def __init__(self, policy_service: PolicyService) -> None:
        self._policy_service = policy_service

    async def execute(self, input: SetPolicyInput, context: ExecutionContext) -> PolicyRow:
        from app.tools.policy import ActionPolicy

        # The AI must never decide what it's allowed to do automatically —
        # role-based permission alone isn't enough here (Role.MANAGER, the
        # role AIExecutionService always assigns, legitimately holds
        # MANAGE_AUTOMATION_POLICIES for its human members), so this is an
        # explicit actor_type check, the same pattern Phase 9 used for
        # approvals.approve/reject.
        if context.actor_type == ActorType.AI:
            raise ValueError("The AI cannot change automation policy — only a human can authorize this")

        detail = await self._policy_service.set_policy(
            context.tenant_id,
            input.tool_name,
            ActionPolicy(input.policy),
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return PolicyRow(**detail)


class AutonomyStatsInput(BaseModel):
    pass


class AutonomyStatsOutput(BaseModel):
    date: str
    automatic: int
    approval_required: int
    blocked: int
    failed: int
    total: int


class GetAutonomyStats(Tool):
    """Owner Cockpit's "Autonomy status" widget — real counts from today's
    `AuditLog` rows for actual `tool.execute:*` actions, never hardcoded.
    Bucketed by the same `result`/`error` fields ToolRegistry._audit()
    already writes for every call — no second bookkeeping mechanism."""

    name = "automation.get_autonomy_stats"
    description = "Count today's tool executions by outcome: automatic, approval required, blocked, failed."
    input_schema = AutonomyStatsInput
    output_schema = AutonomyStatsOutput
    required_permission = Permission.READ_AUTOMATION_POLICIES

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def execute(self, input: AutonomyStatsInput, context: ExecutionContext) -> AutonomyStatsOutput:
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.models.audit_log import AuditLog

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            rows = (
                await session.execute(
                    select(AuditLog.result, AuditLog.input_summary).where(
                        AuditLog.tenant_id == context.tenant_id,
                        AuditLog.action.like("tool.execute:%"),
                        AuditLog.created_at >= today_start,
                    )
                )
            ).all()

        automatic = approval_required = blocked = failed = 0
        for result, input_summary in rows:
            if result == "success":
                automatic += 1
            elif result == "pending_approval":
                approval_required += 1
            elif result == "failure":
                error = (input_summary or {}).get("_error", "")
                if error == "blocked_by_policy":
                    blocked += 1
                else:
                    failed += 1

        total = automatic + approval_required + blocked + failed
        return AutonomyStatsOutput(
            date=today_start.date().isoformat(),
            automatic=automatic,
            approval_required=approval_required,
            blocked=blocked,
            failed=failed,
            total=total,
        )


class ResetPolicyInput(BaseModel):
    tool_name: str


class ResetPolicy(Tool):
    name = "automation.reset_policy"
    description = "Reset one tool's policy back to the system default for this tenant."
    input_schema = ResetPolicyInput
    output_schema = PolicyRow
    required_permission = Permission.MANAGE_AUTOMATION_POLICIES

    def __init__(self, policy_service: PolicyService) -> None:
        self._policy_service = policy_service

    async def execute(self, input: ResetPolicyInput, context: ExecutionContext) -> PolicyRow:
        if context.actor_type == ActorType.AI:
            raise ValueError("The AI cannot change automation policy — only a human can authorize this")

        detail = await self._policy_service.reset_policy(
            context.tenant_id, input.tool_name, actor_id=context.actor_id, correlation_id=context.correlation_id
        )
        return PolicyRow(**detail)
