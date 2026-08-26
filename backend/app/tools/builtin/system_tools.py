from datetime import datetime, timezone

from pydantic import BaseModel

from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class TenantContextOutput(BaseModel):
    tenant_id: str
    actor_type: str
    role: str | None


class GetTenantContext(Tool):
    name = "system.get_tenant_context"
    description = "Return the tenant and actor identity for the current execution context."
    input_schema = EmptyInput
    output_schema = TenantContextOutput
    required_permission = None

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> TenantContextOutput:
        return TenantContextOutput(
            tenant_id=str(context.tenant_id),
            actor_type=context.actor_type,
            role=context.role.value if context.role else None,
        )


class CurrentTimeOutput(BaseModel):
    utc_now: str


class GetCurrentTime(Tool):
    name = "system.get_current_time"
    description = "Return the current UTC time. AI must never assume 'today' without calling this."
    input_schema = EmptyInput
    output_schema = CurrentTimeOutput
    required_permission = None

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> CurrentTimeOutput:
        return CurrentTimeOutput(utc_now=datetime.now(timezone.utc).isoformat())
