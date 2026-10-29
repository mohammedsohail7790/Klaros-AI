"""crm.add_lead_note / crm.list_lead_notes: notes on an enquiry, for a person or an AI crew member to record an assessment before the enquiry is a customer.

A business that handles patient data stores a note only if the person's consent allows storing health information, because a free-text note can carry it, and the text is screened for
safety categories like every other free text. The lead id is accepted under the names a model tends to use for it (`enquiry_id`, `id`), and the note text under the names it tends to use for that (`note`, `content`, `text`, ...).
Every alias is also declared personal, because redaction works by key name and a note can carry health information whichever name the model chose.
"""

import uuid

from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Lead, LeadNote
from app.models.rbac import Permission
from app.services import consent_gate, halla_consent
from app.tools.base import ExecutionContext, Tool


# A model called to write a note often names the text field something other than `body`. The live Lead Responder and Safety Triage failed on exactly this ("body Field required").
NOTE_TEXT_KEYS = ("body", "note", "notes", "content", "text", "message", "assessment", "summary", "comment")


class AddLeadNoteInput(BaseModel):
    lead_id: uuid.UUID = Field(validation_alias=AliasChoices("lead_id", "enquiry_id", "id"))
    body: str = Field(min_length=1, max_length=4000, validation_alias=AliasChoices(*NOTE_TEXT_KEYS))


class AddLeadNoteOutput(BaseModel):
    note_id: str


class AddLeadNote(Tool):
    name = "crm.add_lead_note"
    pii_input_fields = NOTE_TEXT_KEYS
    description = "Attach a freeform note to an enquiry (a lead): an assessment, the next step, or why a person must review it. Use this, not crm.create_note, when the person is not yet a customer."
    input_schema = AddLeadNoteInput
    output_schema = AddLeadNoteOutput
    required_permission = Permission.UPDATE_LEAD

    async def execute(self, input: AddLeadNoteInput, context: ExecutionContext) -> AddLeadNoteOutput:
        tid = context.tenant_id
        async with async_session_maker() as session:
            await set_tenant_context(session, tid)
            lead = (await session.execute(select(Lead).where(Lead.id == input.lead_id, Lead.tenant_id == tid))).scalar_one_or_none()
            if lead is None:
                raise ValueError("Lead not found")
            if await consent_gate.tenant_requires_consent(async_session_maker, tid):
                state = await halla_consent.state_for(session, tid, halla_lead_id=lead.external_id, lead_id=lead.id)
                if not state.allows(consent_gate.STORE_MEDICAL):
                    raise consent_gate.ConsentRequiredError([consent_gate.STORE_MEDICAL])  # a free-text note may carry health information
            note = LeadNote(tenant_id=tid, lead_id=lead.id, author_type=context.actor_type, author_id=context.actor_id, body=input.body)
            session.add(note)
            await session.commit()
            await session.refresh(note)
            note_id = str(note.id)
        from app.services.mt_intake_safety import screen_lead

        await screen_lead(async_session_maker, tid, input.lead_id, input.body, source="lead_note")
        return AddLeadNoteOutput(note_id=note_id)


class ListLeadNotesInput(BaseModel):
    lead_id: uuid.UUID = Field(validation_alias=AliasChoices("lead_id", "enquiry_id", "id"))


class ListLeadNotesOutput(BaseModel):
    notes: list[dict]


class ListLeadNotes(Tool):
    name = "crm.list_lead_notes"
    description = "List the notes on an enquiry (a lead), newest first. Read-only."
    input_schema = ListLeadNotesInput
    output_schema = ListLeadNotesOutput
    required_permission = Permission.READ_LEADS

    async def execute(self, input: ListLeadNotesInput, context: ExecutionContext) -> ListLeadNotesOutput:
        async with async_session_maker() as session:
            await set_tenant_context(session, context.tenant_id)
            rows = (await session.execute(select(LeadNote).where(LeadNote.tenant_id == context.tenant_id, LeadNote.lead_id == input.lead_id).order_by(LeadNote.created_at.desc()))).scalars().all()
        return ListLeadNotesOutput(notes=[{"id": str(n.id), "author_type": n.author_type, "author_id": str(n.author_id) if n.author_id else None, "body": n.body, "created_at": n.created_at.isoformat()} for n in rows])
