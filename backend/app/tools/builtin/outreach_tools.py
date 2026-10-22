"""The hourly outreach run as a crew tool: the engine drafts the next emails, then the sender sends the due ones for campaigns the owner has taken live. A shadow campaign only ever gets drafts."""

from pydantic import BaseModel

from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services.clinic_outreach_engine import ClinicOutreachEngine
from app.services.clinic_outreach_sender import ClinicOutreachSender
from app.tools.base import ExecutionContext, Tool


class RunOutreachEngineInput(BaseModel):
    pass


class RunOutreachEngineOutput(BaseModel):
    campaigns: int
    composed: int
    blocked: int
    skipped: int
    parked: int
    sent: int = 0
    send_failed: int = 0
    send_held: int = 0


class RunOutreachEngine(Tool):
    name = "outreach.run_engine"
    description = (
        "Prepare the next clinic outreach emails for this business's active campaigns, from the owner-approved wording, within the daily limits and each clinic's local send window. "
        "A shadow campaign only gets stored drafts for the owner to review. A campaign the owner has taken live also gets its due emails sent, behind every safety check."
    )
    input_schema = RunOutreachEngineInput
    output_schema = RunOutreachEngineOutput
    required_permission = Permission.MANAGE_CAMPAIGNS

    async def execute(self, input: RunOutreachEngineInput, context: ExecutionContext) -> RunOutreachEngineOutput:
        counts = await ClinicOutreachEngine(async_session_maker).run(context.tenant_id)
        sent = await ClinicOutreachSender(async_session_maker).send_due(context.tenant_id)
        return RunOutreachEngineOutput(**counts, sent=sent["sent"], send_failed=sent["failed"], send_held=sent["held"] + sent["paused"])
