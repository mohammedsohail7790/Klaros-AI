"""notifications.send_owner_digest: the morning e-mail to the owners listing what needs them. Internal only: it reaches the business's own owners, never a patient or a clinic."""

from pydantic import BaseModel

from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class OwnerDigestOutput(BaseModel):
    sent: int
    reason: str | None = None


class SendOwnerDigest(Tool):
    name = "notifications.send_owner_digest"
    description = "E-mail the business owners today's list of what needs them (from the attention queue). Once a day, skipped when nothing needs them, never sent to anyone outside the team."
    input_schema = EmptyInput
    output_schema = OwnerDigestOutput
    required_permission = Permission.MANAGE_COLLECTIONS

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> OwnerDigestOutput:
        from app.services import owner_digest

        out = await owner_digest.send_digest(async_session_maker, context.tenant_id, actor_type=context.actor_type, actor_id=context.actor_id)
        return OwnerDigestOutput(**out)
