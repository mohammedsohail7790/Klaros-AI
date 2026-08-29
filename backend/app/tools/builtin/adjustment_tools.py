import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.finance import CreditNote, WriteOffRequest
from app.models.rbac import Permission
from app.services.adjustments_service import AdjustmentNotFoundError, AdjustmentsService, InvalidAdjustmentError, InvoiceNotFoundError
from app.tools.base import ExecutionContext, Tool


def _credit_note_to_dict(c: CreditNote) -> dict[str, Any]:
    return {
        "id": str(c.id), "invoice_id": str(c.invoice_id), "credit_note_number": c.credit_note_number,
        "reason": c.reason, "total": str(c.total), "status": c.status,
    }


def _writeoff_to_dict(w: WriteOffRequest) -> dict[str, Any]:
    return {
        "id": str(w.id), "invoice_id": str(w.invoice_id), "amount": str(w.amount),
        "reason": w.reason, "status": w.status,
    }


class LineItemModel(BaseModel):
    description: str
    amount: Decimal


class CreateCreditNoteRequestInput(BaseModel):
    invoice_id: uuid.UUID
    reason: str
    line_items: list[LineItemModel]


class CreditNoteOutput(BaseModel):
    credit_note: dict[str, Any]


class CreateCreditNoteRequest(Tool):
    """Never applies directly — always APPROVAL_REQUIRED status with a real
    ApprovalRequest attached."""

    name = "finance.create_credit_note_request"
    description = "Request a credit note against an invoice. Always requires human approval."
    input_schema = CreateCreditNoteRequestInput
    output_schema = CreditNoteOutput
    required_permission = Permission.UPDATE_INVOICE

    def __init__(self, adjustments_service: AdjustmentsService) -> None:
        self._adjustments_service = adjustments_service

    async def execute(self, input: CreateCreditNoteRequestInput, context: ExecutionContext) -> CreditNoteOutput:
        try:
            note = await self._adjustments_service.request_credit_note(
                context.tenant_id, invoice_id=input.invoice_id, reason=input.reason,
                line_items=[(i.description, i.amount) for i in input.line_items],
                requested_by=context.actor_id,
            )
        except (InvoiceNotFoundError, InvalidAdjustmentError) as e:
            raise ValueError(str(e)) from e
        return CreditNoteOutput(credit_note=_credit_note_to_dict(note))


class DecideCreditNoteInput(BaseModel):
    credit_note_id: uuid.UUID


class ApproveCreditNote(Tool):
    """Explicit actor-type guard added Phase 12F — see
    finance.approve_refund's docstring for why role-only gating was found
    insufficient (Role.MANAGER, the only role AIExecutionService has ever
    been invoked with, DOES hold APPROVE_CREDIT_NOTE)."""

    name = "finance.approve_credit_note"
    description = "Approve a requested credit note and apply it to the invoice."
    input_schema = DecideCreditNoteInput
    output_schema = CreditNoteOutput
    required_permission = Permission.APPROVE_CREDIT_NOTE

    def __init__(self, adjustments_service: AdjustmentsService) -> None:
        self._adjustments_service = adjustments_service

    async def execute(self, input: DecideCreditNoteInput, context: ExecutionContext) -> CreditNoteOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot approve a credit note — approval requires a human actor")
        try:
            note = await self._adjustments_service.decide_credit_note(
                context.tenant_id, input.credit_note_id, approved=True, decided_by=context.actor_id
            )
        except (AdjustmentNotFoundError, InvalidAdjustmentError) as e:
            raise ValueError(str(e)) from e
        return CreditNoteOutput(credit_note=_credit_note_to_dict(note))


class RejectCreditNote(Tool):
    name = "finance.reject_credit_note"
    description = "Reject a requested credit note."
    input_schema = DecideCreditNoteInput
    output_schema = CreditNoteOutput
    required_permission = Permission.APPROVE_CREDIT_NOTE

    def __init__(self, adjustments_service: AdjustmentsService) -> None:
        self._adjustments_service = adjustments_service

    async def execute(self, input: DecideCreditNoteInput, context: ExecutionContext) -> CreditNoteOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot reject a credit note — this requires a human actor")
        try:
            note = await self._adjustments_service.decide_credit_note(
                context.tenant_id, input.credit_note_id, approved=False, decided_by=context.actor_id
            )
        except (AdjustmentNotFoundError, InvalidAdjustmentError) as e:
            raise ValueError(str(e)) from e
        return CreditNoteOutput(credit_note=_credit_note_to_dict(note))


class CreateWriteOffRequestInput(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal
    reason: str


class WriteOffOutput(BaseModel):
    writeoff: dict[str, Any]


class CreateWriteOffRequest(Tool):
    """Never applies directly — always REQUESTED with a real ApprovalRequest
    attached. 'Never delete an unpaid invoice to make AR disappear': a
    write-off is the only sanctioned way to zero out uncollectable AR, and
    even that requires human approval."""

    name = "finance.create_writeoff_request"
    description = "Request a write-off against an invoice's outstanding balance. Always requires human approval."
    input_schema = CreateWriteOffRequestInput
    output_schema = WriteOffOutput
    required_permission = Permission.UPDATE_INVOICE

    def __init__(self, adjustments_service: AdjustmentsService) -> None:
        self._adjustments_service = adjustments_service

    async def execute(self, input: CreateWriteOffRequestInput, context: ExecutionContext) -> WriteOffOutput:
        try:
            writeoff = await self._adjustments_service.request_writeoff(
                context.tenant_id, invoice_id=input.invoice_id, amount=input.amount, reason=input.reason,
                requested_by=context.actor_id,
            )
        except (InvoiceNotFoundError, InvalidAdjustmentError) as e:
            raise ValueError(str(e)) from e
        return WriteOffOutput(writeoff=_writeoff_to_dict(writeoff))


class DecideWriteOffInput(BaseModel):
    writeoff_id: uuid.UUID


class ApproveWriteOff(Tool):
    """Explicit actor-type guard added Phase 12F — see
    finance.approve_refund's docstring for why role-only gating was found
    insufficient."""

    name = "finance.approve_writeoff"
    description = "Approve a requested write-off and reduce the invoice's amount due."
    input_schema = DecideWriteOffInput
    output_schema = WriteOffOutput
    required_permission = Permission.APPROVE_WRITEOFF

    def __init__(self, adjustments_service: AdjustmentsService) -> None:
        self._adjustments_service = adjustments_service

    async def execute(self, input: DecideWriteOffInput, context: ExecutionContext) -> WriteOffOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot approve a write-off — approval requires a human actor")
        try:
            writeoff = await self._adjustments_service.decide_writeoff(
                context.tenant_id, input.writeoff_id, approved=True, decided_by=context.actor_id
            )
        except (AdjustmentNotFoundError, InvalidAdjustmentError) as e:
            raise ValueError(str(e)) from e
        return WriteOffOutput(writeoff=_writeoff_to_dict(writeoff))


class RejectWriteOff(Tool):
    name = "finance.reject_writeoff"
    description = "Reject a requested write-off."
    input_schema = DecideWriteOffInput
    output_schema = WriteOffOutput
    required_permission = Permission.APPROVE_WRITEOFF

    def __init__(self, adjustments_service: AdjustmentsService) -> None:
        self._adjustments_service = adjustments_service

    async def execute(self, input: DecideWriteOffInput, context: ExecutionContext) -> WriteOffOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot reject a write-off — this requires a human actor")
        try:
            writeoff = await self._adjustments_service.decide_writeoff(
                context.tenant_id, input.writeoff_id, approved=False, decided_by=context.actor_id
            )
        except (AdjustmentNotFoundError, InvalidAdjustmentError) as e:
            raise ValueError(str(e)) from e
        return WriteOffOutput(writeoff=_writeoff_to_dict(writeoff))
