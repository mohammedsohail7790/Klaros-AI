"""workforce.* tools: let a governed agent ask the tenant's AI receptionist to place an outbound call.

The call itself is `HallaIntegrationService.call_lead`, the same code path as the "Ask Halla to call" button, so its rules apply unchanged: the lead needs a phone
number, a consent-gated tenant needs contact consent on record, and nothing happens unless the workforce platform is connected. This tool adds the limits an
autonomous caller needs and a person pressing a button does not: a lead is never called again within 24 hours, and never more than three times in all.
Expected refusals come back as `requested=False` with a reason (so the agent can note it and move on) rather than as errors.

A call reaches a real person, so the tool's default policy is APPROVAL_REQUIRED: a person approves each call. A tenant that wants its crew to call on its own
sets the tool to AUTO through the tenant automation policy (`automation.set_policy`); that is the tenant's decision and is recorded in the audit log.
"""

import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.workforce.contract import WorkforceNotConnectedError, WorkforceUnavailableError
from app.models.audit_log import AuditLog
from app.models.rbac import Permission
from app.services.halla_integration_service import HallaIntegrationService, LeadNotCallableError
from app.tools.base import ExecutionContext, Tool

TOOL_NAME = "workforce.call_lead"
MIN_HOURS_BETWEEN_CALLS = 24
MAX_CALLS_PER_LEAD = 3


class CallLeadInput(BaseModel):
    lead_id: uuid.UUID
    reason: str = Field(default="follow_up", max_length=60)
    opening_context: str | None = Field(default=None, max_length=1000)


class CallLeadOutput(BaseModel):
    requested: bool
    call_id: str | None = None
    skipped_reason: str | None = None


class CallLead(Tool):
    name = TOOL_NAME
    description = (
        "Ask the business's AI receptionist to phone a lead who has enquired, for example to qualify or follow up. Needs a phone number, contact consent where the "
        "business requires it, and a connected receptionist. A lead is not called twice within 24 hours or more than three times."
    )
    input_schema = CallLeadInput
    output_schema = CallLeadOutput
    required_permission = Permission.UPDATE_LEAD

    def __init__(self, session_factory: async_sessionmaker, halla: HallaIntegrationService) -> None:
        self._session_factory = session_factory
        self._halla = halla

    async def _recent_calls(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> tuple[int, datetime | None]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            q = select(func.count(), func.max(AuditLog.created_at)).where(
                AuditLog.tenant_id == tenant_id, AuditLog.tool == TOOL_NAME, AuditLog.entity_id == lead_id, AuditLog.result == "success",
            )
            count, last = (await session.execute(q)).one()
        return int(count or 0), last

    async def execute(self, input: CallLeadInput, context: ExecutionContext) -> CallLeadOutput:
        count, last = await self._recent_calls(context.tenant_id, input.lead_id)
        if count >= MAX_CALLS_PER_LEAD:
            return CallLeadOutput(requested=False, skipped_reason="This lead has already been called the maximum number of times.")
        if last is not None:
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last < timedelta(hours=MIN_HOURS_BETWEEN_CALLS):
                return CallLeadOutput(requested=False, skipped_reason="This lead was called in the last 24 hours.")
        try:
            result = await self._halla.call_lead(context.tenant_id, input.lead_id, reason=input.reason, opening_context=input.opening_context)
        except (LeadNotCallableError, WorkforceNotConnectedError, WorkforceUnavailableError) as exc:
            return CallLeadOutput(requested=False, skipped_reason=str(exc) or type(exc).__name__)
        return CallLeadOutput(requested=bool(result.get("requested")), call_id=result.get("call_id"))
