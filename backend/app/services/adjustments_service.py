"""section 11: credit notes and write-offs — both APPROVAL_REQUIRED by
default, same shape as `PaymentService.request_refund`/`decide_refund`:
create always lands in an approval-required state and creates a real
`ApprovalRequest` row; a dedicated decide method both resolves that
request and applies the effect in one transaction.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.finance import (
    CreditNote,
    CreditNoteLineItem,
    CreditNoteStatus,
    Invoice,
    InvoiceStatus,
    WriteOffRequest,
    WriteOffStatus,
)
from app.services.approval_helper import create_approval_request


class InvoiceNotFoundError(Exception):
    pass


class AdjustmentNotFoundError(Exception):
    pass


class InvalidAdjustmentError(Exception):
    pass


class AdjustmentsService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def _next_credit_note_number(self, session, tenant_id: uuid.UUID) -> str:
        from sqlalchemy import func

        count = (
            await session.execute(select(func.count(CreditNote.id)).where(CreditNote.tenant_id == tenant_id))
        ).scalar_one()
        return f"CN-{1000 + count + 1}"

    async def request_credit_note(
        self,
        tenant_id: uuid.UUID,
        *,
        invoice_id: uuid.UUID,
        reason: str,
        line_items: list[tuple[str, Decimal]],
        requested_by: uuid.UUID | None,
    ) -> CreditNote:
        total = sum((amount for _, amount in line_items), Decimal("0"))
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if total > invoice.total:
                raise InvalidAdjustmentError("Credit note total cannot exceed the invoice total")

            note = CreditNote(
                tenant_id=tenant_id,
                invoice_id=invoice_id,
                customer_id=invoice.customer_id,
                credit_note_number=await self._next_credit_note_number(session, tenant_id),
                reason=reason,
                total=total,
                status=CreditNoteStatus.APPROVAL_REQUIRED,
                requested_by=requested_by,
            )
            session.add(note)
            await session.flush()

            for description, amount in line_items:
                session.add(
                    CreditNoteLineItem(
                        tenant_id=tenant_id, credit_note_id=note.id, description=description, amount=amount
                    )
                )

            await create_approval_request(
                session,
                tenant_id=tenant_id,
                requested_by_type="USER",
                requested_by_id=requested_by,
                tool_name="finance.approve_credit_note",
                action_type="credit_note_approval",
                reason=f"Credit note of ${total} requested for invoice {invoice.invoice_number}: {reason}",
                tool_input={"credit_note_id": str(note.id), "invoice_id": str(invoice_id), "amount": str(total)},
            )

            await session.commit()
            await session.refresh(note)
        return note

    async def decide_credit_note(
        self, tenant_id: uuid.UUID, credit_note_id: uuid.UUID, *, approved: bool, decided_by: uuid.UUID | None
    ) -> CreditNote:
        from app.models.approval import ApprovalRequest, ApprovalStatus

        async with self._session_factory() as session:
            note = await session.get(CreditNote, credit_note_id)
            if note is None or note.tenant_id != tenant_id:
                raise AdjustmentNotFoundError("Credit note not found")
            if note.status != CreditNoteStatus.APPROVAL_REQUIRED:
                raise InvalidAdjustmentError("Credit note is not pending approval")

            pending = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id,
                        ApprovalRequest.tool_name == "finance.approve_credit_note",
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for req in pending:
                if req.tool_input.get("credit_note_id") == str(credit_note_id):
                    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    req.decided_by = decided_by

            if approved:
                note.status = CreditNoteStatus.APPLIED
                note.approved_by = decided_by
                invoice = await session.get(Invoice, note.invoice_id)
                if invoice is not None:
                    invoice.amount_due -= note.total
                    invoice.total -= note.total
                    if invoice.amount_due <= 0:
                        invoice.status = InvoiceStatus.PAID
            else:
                note.status = CreditNoteStatus.VOID

            await session.commit()
            await session.refresh(note)
        return note

    async def request_writeoff(
        self,
        tenant_id: uuid.UUID,
        *,
        invoice_id: uuid.UUID,
        amount: Decimal,
        reason: str,
        requested_by: uuid.UUID | None,
    ) -> WriteOffRequest:
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None or invoice.tenant_id != tenant_id:
                raise InvoiceNotFoundError("Invoice not found")
            if amount > invoice.amount_due:
                raise InvalidAdjustmentError("Write-off amount cannot exceed the invoice's amount due")

            writeoff = WriteOffRequest(
                tenant_id=tenant_id,
                invoice_id=invoice_id,
                amount=amount,
                reason=reason,
                status=WriteOffStatus.REQUESTED,
                requested_by=requested_by,
            )
            session.add(writeoff)
            await session.flush()

            await create_approval_request(
                session,
                tenant_id=tenant_id,
                requested_by_type="USER",
                requested_by_id=requested_by,
                tool_name="finance.approve_writeoff",
                action_type="writeoff_approval",
                reason=f"Write-off of ${amount} requested for invoice {invoice.invoice_number}: {reason}",
                tool_input={"writeoff_id": str(writeoff.id), "invoice_id": str(invoice_id), "amount": str(amount)},
            )

            await session.commit()
            await session.refresh(writeoff)
        return writeoff

    async def decide_writeoff(
        self, tenant_id: uuid.UUID, writeoff_id: uuid.UUID, *, approved: bool, decided_by: uuid.UUID | None
    ) -> WriteOffRequest:
        from app.models.approval import ApprovalRequest, ApprovalStatus

        async with self._session_factory() as session:
            writeoff = await session.get(WriteOffRequest, writeoff_id)
            if writeoff is None or writeoff.tenant_id != tenant_id:
                raise AdjustmentNotFoundError("Write-off request not found")
            if writeoff.status != WriteOffStatus.REQUESTED:
                raise InvalidAdjustmentError("Write-off is not pending")

            pending = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id,
                        ApprovalRequest.tool_name == "finance.approve_writeoff",
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for req in pending:
                if req.tool_input.get("writeoff_id") == str(writeoff_id):
                    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    req.decided_by = decided_by

            if approved:
                writeoff.status = WriteOffStatus.APPLIED
                writeoff.approved_by = decided_by
                invoice = await session.get(Invoice, writeoff.invoice_id)
                if invoice is not None:
                    invoice.amount_due -= writeoff.amount
                    if invoice.amount_due <= 0:
                        invoice.status = InvoiceStatus.CANCELLED
            else:
                writeoff.status = WriteOffStatus.REJECTED

            await session.commit()
            await session.refresh(writeoff)
        return writeoff
