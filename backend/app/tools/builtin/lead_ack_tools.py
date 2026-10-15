"""crm.acknowledge_enquiry: a short "we received your enquiry" e-mail to the person who made it. The same for every business.

It goes through the consent-guarded communication channel, so a business that handles patient data only e-mails someone whose contact consent is on record, and it is bound to that lead.
It says nothing about what the person asked for (that can be medical detail) and promises nothing. If no real e-mail provider is configured it reports that instead of pretending, and a lead is
acknowledged at most once. APPROVAL_REQUIRED by default because it reaches a real person; a business that wants instant replies sets it to AUTO in its own automation policy.
"""

import uuid

from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import func, select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.models.crm import Lead
from app.models.organization import Organization
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool

TOOL_NAME = "crm.acknowledge_enquiry"
SENT_ACTION = "crm.enquiry_acknowledged"  # written only when an e-mail was really sent: a refused or skipped attempt must not count as done


class AcknowledgeEnquiryInput(BaseModel):
    lead_id: uuid.UUID = Field(validation_alias=AliasChoices("lead_id", "enquiry_id", "id"))  # a model often calls it the enquiry id


class AcknowledgeEnquiryOutput(BaseModel):
    sent: bool
    reason: str | None = None


class AcknowledgeEnquiry(Tool):
    name = TOOL_NAME
    description = (
        "E-mail the person behind a new enquiry to confirm it was received and that someone will be in touch. Sent once per lead, only if their contact consent allows it, and it mentions "
        "nothing they asked about."
    )
    input_schema = AcknowledgeEnquiryInput
    output_schema = AcknowledgeEnquiryOutput
    required_permission = Permission.UPDATE_LEAD

    async def execute(self, input: AcknowledgeEnquiryInput, context: ExecutionContext) -> AcknowledgeEnquiryOutput:
        from app.communications.base import MessageTemplate
        from app.communications.email_config import email_configured
        from app.communications.factory import get_communication_provider

        tid = context.tenant_id
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            lead = (await session.execute(select(Lead).where(Lead.id == input.lead_id, Lead.tenant_id == tid))).scalar_one_or_none()
            org = await session.get(Organization, tid)
            done = (await session.execute(select(func.count()).select_from(AuditLog).where(
                AuditLog.tenant_id == tid, AuditLog.action == SENT_ACTION, AuditLog.entity_id == input.lead_id))).scalar_one()
        if lead is None:
            return AcknowledgeEnquiryOutput(sent=False, reason="enquiry not found")
        to = (lead.email or "").strip()
        if not to:
            return AcknowledgeEnquiryOutput(sent=False, reason="the enquiry has no e-mail address")
        if done:
            return AcknowledgeEnquiryOutput(sent=False, reason="this enquiry was already acknowledged")
        if not email_configured():
            return AcknowledgeEnquiryOutput(sent=False, reason="no real e-mail provider is configured on this deployment")
        business = org.name if org is not None else "our team"
        first = (lead.name or "").strip().split(" ")[0] or "there"
        body = (
            f"Hello {first},\n\nThank you for getting in touch with {business}. We have received your enquiry, and a member of our team will contact you soon.\n\n"
            "If you did not make this enquiry, please reply to let us know.\n\n" + business
        )
        result = await get_communication_provider(async_session_maker).deliver_email(
            tid, to=to, subject=f"We received your enquiry - {business}", body=body, template=MessageTemplate.LEAD_FOLLOW_UP, lead_id=lead.id,
        )
        if result.blocked:
            return AcknowledgeEnquiryOutput(sent=False, reason="no contact consent is on record for this person")
        if not result.sent:
            return AcknowledgeEnquiryOutput(sent=False, reason="the e-mail provider did not deliver the message")
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            session.add(AuditLog(tenant_id=tid, actor_type=context.actor_type, actor_id=context.actor_id, action=SENT_ACTION, entity_type="lead", entity_id=lead.id,
                                 input_summary={}, result="success"))
            await session.commit()
        return AcknowledgeEnquiryOutput(sent=True)
