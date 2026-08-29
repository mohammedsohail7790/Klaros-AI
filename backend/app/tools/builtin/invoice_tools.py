import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.finance import Invoice
from app.models.rbac import Permission
from app.invoice_delivery.factory import get_invoice_delivery_provider
from app.services.invoice_service import (
    InvalidInvoiceTransitionError,
    InvoiceNotFoundError,
    InvoiceService,
    JobNotFoundError,
    LineItemInput,
)
from app.tools.base import ExecutionContext, Tool


def _invoice_to_dict(inv: Invoice) -> dict[str, Any]:
    return {
        "id": str(inv.id),
        "invoice_number": inv.invoice_number,
        "customer_id": str(inv.customer_id),
        "job_id": str(inv.job_id) if inv.job_id else None,
        "status": inv.status,
        "issue_date": inv.issue_date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "subtotal": str(inv.subtotal),
        "tax": str(inv.tax),
        "discount": str(inv.discount),
        "total": str(inv.total),
        "amount_paid": str(inv.amount_paid),
        "amount_due": str(inv.amount_due),
        "notes": inv.notes,
    }


class LineItemModel(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")


def _to_line_items(items: list[LineItemModel]) -> list[LineItemInput]:
    return [
        LineItemInput(
            description=i.description, quantity=i.quantity, unit_price=i.unit_price,
            discount=i.discount, tax_rate=i.tax_rate,
        )
        for i in items
    ]


class InvoiceOutput(BaseModel):
    invoice: dict[str, Any]
    deduplicated: bool = False


class TriggerInvoiceFromJobInput(BaseModel):
    job_id: uuid.UUID


class TriggerInvoiceFromJob(Tool):
    """Manual/AI-callable equivalent of the `invoice.trigger_requested`
    event handler — same idempotent `create_draft_from_job`, so calling
    this twice for the same job never creates a duplicate invoice."""

    name = "finance.trigger_invoice_from_job"
    description = "Create a DRAFT invoice from a job's real pricing data. Idempotent per job."
    input_schema = TriggerInvoiceFromJobInput
    output_schema = InvoiceOutput
    required_permission = Permission.CREATE_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: TriggerInvoiceFromJobInput, context: ExecutionContext) -> InvoiceOutput:
        try:
            invoice, deduped = await self._invoice_service.create_draft_from_job(context.tenant_id, input.job_id)
        except JobNotFoundError as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice), deduplicated=deduped)


class CreateInvoiceDraftInput(BaseModel):
    customer_id: uuid.UUID
    job_id: uuid.UUID | None = None
    line_items: list[LineItemModel]
    due_date: str | None = None


class CreateInvoiceDraft(Tool):
    name = "finance.create_invoice_draft"
    description = "Create a DRAFT invoice directly from line items (manual, not job-triggered)."
    input_schema = CreateInvoiceDraftInput
    output_schema = InvoiceOutput
    required_permission = Permission.CREATE_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: CreateInvoiceDraftInput, context: ExecutionContext) -> InvoiceOutput:
        invoice = await self._invoice_service.create_manual_draft(
            context.tenant_id,
            customer_id=input.customer_id,
            job_id=input.job_id,
            items=_to_line_items(input.line_items),
        )
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class UpdateInvoiceDraftInput(BaseModel):
    invoice_id: uuid.UUID
    line_items: list[LineItemModel]


class UpdateInvoiceDraft(Tool):
    name = "finance.update_invoice_draft"
    description = "Replace a DRAFT invoice's line items; totals are always recalculated server-side."
    input_schema = UpdateInvoiceDraftInput
    output_schema = InvoiceOutput
    required_permission = Permission.UPDATE_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: UpdateInvoiceDraftInput, context: ExecutionContext) -> InvoiceOutput:
        try:
            invoice = await self._invoice_service.update_draft(
                context.tenant_id, input.invoice_id, _to_line_items(input.line_items)
            )
        except (InvoiceNotFoundError, InvalidInvoiceTransitionError) as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class RequestInvoiceApprovalInput(BaseModel):
    invoice_id: uuid.UUID


class RequestInvoiceApproval(Tool):
    """AUTO at the policy layer — the body itself decides AUTO-approve vs.
    PENDING_APPROVAL using `app/services/invoice_policy.py`'s deterministic
    thresholds. See that module's docstring for why this can't be a static
    ToolRegistry policy entry."""

    name = "finance.request_invoice_approval"
    description = "Submit a DRAFT invoice for approval; auto-approves under threshold, else creates an ApprovalRequest."
    input_schema = RequestInvoiceApprovalInput
    output_schema = InvoiceOutput
    required_permission = Permission.UPDATE_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: RequestInvoiceApprovalInput, context: ExecutionContext) -> InvoiceOutput:
        try:
            invoice = await self._invoice_service.request_approval(
                context.tenant_id, input.invoice_id, context.actor_id
            )
        except (InvoiceNotFoundError, InvalidInvoiceTransitionError) as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class DecideInvoiceApprovalInput(BaseModel):
    invoice_id: uuid.UUID
    note: str | None = None


class ApproveInvoice(Tool):
    """Gated by APPROVE_INVOICE AND an explicit actor-type check (Phase
    12F) — the role-only claim ("the AI boundary's role never grants it")
    was found false for the sibling finance.approve_refund tool
    (Role.MANAGER, the only role AIExecutionService has ever been invoked
    with, DOES hold APPROVE_REFUND); fixed here defensively too, matching
    the explicit ActorType.AI guard already used by the generic
    ApproveAction/RejectAction tools."""

    name = "finance.approve_invoice"
    description = "Approve an invoice pending approval."
    input_schema = DecideInvoiceApprovalInput
    output_schema = InvoiceOutput
    required_permission = Permission.APPROVE_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: DecideInvoiceApprovalInput, context: ExecutionContext) -> InvoiceOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot approve an invoice — approval requires a human actor")
        try:
            invoice = await self._invoice_service.decide_approval(
                context.tenant_id, input.invoice_id, approved=True, decided_by=context.actor_id, note=input.note
            )
        except (InvoiceNotFoundError, InvalidInvoiceTransitionError) as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class RejectInvoice(Tool):
    name = "finance.reject_invoice"
    description = "Reject an invoice pending approval, returning it to DRAFT."
    input_schema = DecideInvoiceApprovalInput
    output_schema = InvoiceOutput
    required_permission = Permission.APPROVE_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: DecideInvoiceApprovalInput, context: ExecutionContext) -> InvoiceOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot reject an invoice — this requires a human actor")
        try:
            invoice = await self._invoice_service.decide_approval(
                context.tenant_id, input.invoice_id, approved=False, decided_by=context.actor_id, note=input.note
            )
        except (InvoiceNotFoundError, InvalidInvoiceTransitionError) as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class SendInvoiceInput(BaseModel):
    invoice_id: uuid.UUID


class SendInvoice(Tool):
    """APPROVAL_REQUIRED by default policy — sending is customer-visible and
    can't be undone, so the ToolRegistry intercepts before this body runs."""

    name = "finance.send_invoice"
    description = "Send an APPROVED invoice to the customer via the invoice delivery provider."
    input_schema = SendInvoiceInput
    output_schema = InvoiceOutput
    required_permission = Permission.SEND_INVOICE

    def __init__(self, invoice_service: InvoiceService, session_factory) -> None:
        self._invoice_service = invoice_service
        self._delivery = get_invoice_delivery_provider(session_factory)

    async def execute(self, input: SendInvoiceInput, context: ExecutionContext) -> InvoiceOutput:
        try:
            invoice = await self._invoice_service.send_invoice(context.tenant_id, input.invoice_id, self._delivery)
        except (InvoiceNotFoundError, InvalidInvoiceTransitionError) as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class VoidInvoiceInput(BaseModel):
    invoice_id: uuid.UUID
    reason: str


class VoidInvoice(Tool):
    """APPROVAL_REQUIRED by default policy — voiding is destructive to the
    financial record and can't be undone."""

    name = "finance.void_invoice"
    description = "Void an invoice (not PAID/VOID/CANCELLED already)."
    input_schema = VoidInvoiceInput
    output_schema = InvoiceOutput
    required_permission = Permission.VOID_INVOICE

    def __init__(self, invoice_service: InvoiceService) -> None:
        self._invoice_service = invoice_service

    async def execute(self, input: VoidInvoiceInput, context: ExecutionContext) -> InvoiceOutput:
        try:
            invoice = await self._invoice_service.void_invoice(context.tenant_id, input.invoice_id, input.reason)
        except (InvoiceNotFoundError, InvalidInvoiceTransitionError) as e:
            raise ValueError(str(e)) from e
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))


class GetInvoiceInput(BaseModel):
    invoice_id: uuid.UUID


class GetInvoice(Tool):
    name = "finance.get_invoice"
    description = "Fetch an invoice by id."
    input_schema = GetInvoiceInput
    output_schema = InvoiceOutput
    required_permission = Permission.VIEW_FINANCIALS

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetInvoiceInput, context: ExecutionContext) -> InvoiceOutput:
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, input.invoice_id)
        if invoice is None or invoice.tenant_id != context.tenant_id:
            raise ValueError("Invoice not found")
        return InvoiceOutput(invoice=_invoice_to_dict(invoice))
