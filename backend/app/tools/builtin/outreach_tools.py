"""The daily outreach engine as a crew tool. It stores shadow drafts for review and sends nothing."""

from pydantic import BaseModel

from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services.clinic_outreach_engine import ClinicOutreachEngine
from app.tools.base import ExecutionContext, Tool


class RunOutreachEngineInput(BaseModel):
    pass


class RunOutreachEngineOutput(BaseModel):
    campaigns: int
    composed: int
    blocked: int
    skipped: int
    parked: int


class RunOutreachEngine(Tool):
    name = "outreach.run_engine"
    description = (
        "Prepare the next clinic outreach drafts for this business's active shadow campaigns, from the owner-approved wording, within the daily limits and each clinic's local send window. "
        "It stores drafts for the owner to review and sends nothing."
    )
    input_schema = RunOutreachEngineInput
    output_schema = RunOutreachEngineOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    async def execute(self, input: RunOutreachEngineInput, context: ExecutionContext) -> RunOutreachEngineOutput:
        counts = await ClinicOutreachEngine(async_session_maker).run(context.tenant_id)
        return RunOutreachEngineOutput(**counts)
