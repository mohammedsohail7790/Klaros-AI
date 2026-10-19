"""communications.send_whatsapp: one WhatsApp message to ONE enquiry or customer. The same for every business.

It goes through the consent-guarded communication channel (`get_communication_provider(...).deliver_whatsapp`), bound to the lead or customer, so a business that handles patient data only messages
someone whose contact consent is on record. The model never supplies a phone number: the number is read from the lead or customer record, so it can only reach a person the business already has.
WhatsApp's own rule is modelled honestly: a free-form message is sent only within 24 hours of the person's last message; otherwise an approved template (`template_name`) is required, and without
one nothing is sent and the result says `outside_24h_window_template_required`. APPROVAL_REQUIRED by default because it reaches a real person. Every attempt writes its own audit row (no text, no number).
"""

import uuid

from pydantic import AliasChoices, BaseModel, Field, model_validator
from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.models.crm import Customer, Lead
from app.models.rbac import Permission
from app.tools.base import ExecutionContext, Tool

TOOL_NAME = "communications.send_whatsapp"
SENT_ACTION = "communications.whatsapp_sent"
REFUSED_ACTION = "communications.whatsapp_not_sent"


class SendWhatsAppInput(BaseModel):
    lead_id: uuid.UUID | None = Field(default=None, validation_alias=AliasChoices("lead_id", "enquiry_id"))
    customer_id: uuid.UUID | None = None
    message: str = Field(default="", max_length=1000, description="Free-form text. Only deliverable within 24 hours of the person's last WhatsApp message to the business.")
    template_name: str | None = Field(default=None, max_length=512, description="The name of an APPROVED WhatsApp template. Required to message outside the 24 hour window.")
    template_language: str = Field(default="en_US", max_length=16)
    template_params: list[str] = Field(default_factory=list, max_length=10, description="Values for the template's {{1}}, {{2}} ... slots, in order.")

    @model_validator(mode="after")
    def _one_target(self):
        if self.lead_id is None and self.customer_id is None:
            raise ValueError("give a lead_id or a customer_id")
        if not (self.template_name or "").strip() and not self.message.strip():
            raise ValueError("give a message, or a template_name")
        return self


class SendWhatsAppOutput(BaseModel):
    sent: bool
    reason: str | None = None  # a machine code, e.g. outside_24h_window_template_required, no_contact_consent, whatsapp_not_connected
    mode: str | None = None  # text | template
    message_id: str | None = None


class SendWhatsApp(Tool):
    name = TOOL_NAME
    description = (
        "Send one WhatsApp message to the person behind an enquiry (lead_id) or a customer (customer_id), using the number on their record. Only sent if their contact consent allows it. "
        "Free text works only within 24 hours of their last WhatsApp message; otherwise pass the name of an approved template (template_name, template_params). If neither is possible nothing "
        "is sent and the reason says so."
    )
    input_schema = SendWhatsAppInput
    output_schema = SendWhatsAppOutput
    required_permission = Permission.UPDATE_LEAD
    pii_input_fields = ("message", "template_params")

    async def execute(self, input: SendWhatsAppInput, context: ExecutionContext) -> SendWhatsAppOutput:
        from app.communications.base import MessageTemplate
        from app.communications.factory import get_communication_provider

        tid = context.tenant_id
        mode = "template" if (input.template_name or "").strip() else "text"
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            lead = (await session.execute(select(Lead).where(Lead.id == input.lead_id, Lead.tenant_id == tid))).scalar_one_or_none() if input.lead_id else None
            customer = (await session.execute(select(Customer).where(Customer.id == input.customer_id, Customer.tenant_id == tid))).scalar_one_or_none() if input.customer_id else None
            if lead is not None and customer is None and lead.customer_id is not None and not (lead.phone or "").strip():
                customer = await session.get(Customer, lead.customer_id)
        entity_type, entity_id = ("lead", input.lead_id) if input.lead_id else ("customer", input.customer_id)

        async def finish(out: SendWhatsAppOutput) -> SendWhatsAppOutput:
            async with async_session_maker() as session:
                await set_tenant_context(session, tid)
                session.add(AuditLog(tenant_id=tid, actor_type=context.actor_type, actor_id=context.actor_id, action=SENT_ACTION if out.sent else REFUSED_ACTION, entity_type=entity_type, entity_id=entity_id,
                                     input_summary={"mode": mode, "reason": out.reason}, result="success" if out.sent else "failure"))
                await session.commit()
            return out

        if (input.lead_id and lead is None) or (input.customer_id and customer is None and lead is None):
            return await finish(SendWhatsAppOutput(sent=False, reason="person_not_found", mode=mode))
        phone = ((lead.phone if lead is not None else None) or (customer.phone if customer is not None else None) or "").strip()
        if not phone:
            return await finish(SendWhatsAppOutput(sent=False, reason="no_phone_number_on_record", mode=mode))
        result = await get_communication_provider(async_session_maker).deliver_whatsapp(
            tid, to=phone, body=input.message.strip(), template=MessageTemplate.LEAD_FOLLOW_UP,
            lead_id=lead.id if lead is not None else None, customer_id=input.customer_id if lead is None else None,
            template_name=(input.template_name or "").strip() or None, template_language=input.template_language, template_params=tuple(input.template_params),
        )
        if result.blocked:
            return await finish(SendWhatsAppOutput(sent=False, reason=result.reason or "no_contact_consent", mode=mode))
        if not result.sent:
            return await finish(SendWhatsAppOutput(sent=False, reason=result.reason or "whatsapp_send_failed", mode=mode))
        return await finish(SendWhatsAppOutput(sent=True, mode=mode, message_id=result.external_id))
