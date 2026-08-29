import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.finance import Payment, Refund
from app.models.rbac import Permission
from app.payments.internal_test_adapter import InternalTestPaymentAdapter
from app.services.payment_service import (
    AllocationInput,
    InvalidRefundError,
    InvoiceNotFoundError,
    OverpaymentError,
    PaymentNotFoundError,
    PaymentService,
)
from app.tools.base import ExecutionContext, Tool


def _payment_to_dict(p: Payment) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "customer_id": str(p.customer_id),
        "amount": str(p.amount),
        "status": p.status,
        "provider": p.provider,
        "external_id": p.external_id,
        "received_at": p.received_at.isoformat(),
    }


def _refund_to_dict(r: Refund) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "payment_id": str(r.payment_id),
        "invoice_id": str(r.invoice_id) if r.invoice_id else None,
        "amount": str(r.amount),
        "reason": r.reason,
        "status": r.status,
    }


class AllocationModel(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal


class RecordTestPaymentInput(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal
    allocations: list[AllocationModel]
    payment_method: str = "internal_test"


class PaymentOutput(BaseModel):
    payment: dict[str, Any]
    deduplicated: bool = False


class RecordTestPayment(Tool):
    """Uses the INTERNAL TEST PAYMENT PROVIDER only — no real card is
    charged, no money moves. Real providers (Stripe/PayPal/Bill.com) are
    NOT_CONNECTED. Idempotent on (provider, external_id)."""

    name = "finance.record_test_payment"
    description = "Record a payment via the internal test payment provider and allocate it to invoices."
    input_schema = RecordTestPaymentInput
    output_schema = PaymentOutput
    required_permission = Permission.RECORD_PAYMENT

    def __init__(self, payment_service: PaymentService) -> None:
        self._payment_service = payment_service
        self._provider = InternalTestPaymentAdapter()

    async def execute(self, input: RecordTestPaymentInput, context: ExecutionContext) -> PaymentOutput:
        charge = await self._provider.charge(
            context.tenant_id, invoice_id=input.allocations[0].invoice_id if input.allocations else uuid.uuid4(),
            amount=input.amount, method=input.payment_method,
        )
        try:
            payment, deduped = await self._payment_service.record_payment(
                context.tenant_id,
                customer_id=input.customer_id,
                amount=input.amount,
                provider=charge.provider,
                external_id=charge.external_id,
                payment_method=input.payment_method,
                allocations=[AllocationInput(invoice_id=a.invoice_id, amount=a.amount) for a in input.allocations],
            )
        except (InvoiceNotFoundError, OverpaymentError) as e:
            raise ValueError(str(e)) from e
        return PaymentOutput(payment=_payment_to_dict(payment), deduplicated=deduped)


class CreateRefundRequestInput(BaseModel):
    payment_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    amount: Decimal
    reason: str


class RefundOutput(BaseModel):
    refund: dict[str, Any]


class CreateRefundRequest(Tool):
    """Never issues a refund directly — always lands as REQUESTED with a
    real ApprovalRequest attached; see `PaymentService.request_refund`.
    'Never allow AI to issue refunds directly' is enforced here: this tool
    can only ever create a pending request, never complete one."""

    name = "finance.create_refund_request"
    description = "Request a refund against a payment. Always requires human approval before it takes effect."
    input_schema = CreateRefundRequestInput
    output_schema = RefundOutput
    required_permission = Permission.RECORD_PAYMENT

    def __init__(self, payment_service: PaymentService) -> None:
        self._payment_service = payment_service

    async def execute(self, input: CreateRefundRequestInput, context: ExecutionContext) -> RefundOutput:
        try:
            refund = await self._payment_service.request_refund(
                context.tenant_id,
                payment_id=input.payment_id,
                invoice_id=input.invoice_id,
                amount=input.amount,
                reason=input.reason,
                requested_by=context.actor_id,
            )
        except (PaymentNotFoundError, InvalidRefundError) as e:
            raise ValueError(str(e)) from e
        return RefundOutput(refund=_refund_to_dict(refund))


class DecideRefundInput(BaseModel):
    refund_id: uuid.UUID


class ApproveRefund(Tool):
    """Gated by APPROVE_REFUND (a role-level permission) AND an explicit
    actor-type check (Phase 12F) — relying on role alone was a real,
    proven gap: `Role.MANAGER` (the only role `AIExecutionService` has
    ever been invoked with in this codebase) DOES hold `APPROVE_REFUND`,
    so an AI-originated call with that role could reach and complete this
    tool with no human ever involved, discovered and closed this phase.
    Matches the explicit `ActorType.AI` guard already used by the generic
    `ApproveAction`/`RejectAction` tools (app/tools/builtin/approval_tools.py)
    — this tool needed the identical guard and didn't have it."""

    name = "finance.approve_refund"
    description = "Approve a requested refund."
    input_schema = DecideRefundInput
    output_schema = RefundOutput
    required_permission = Permission.APPROVE_REFUND

    def __init__(self, payment_service: PaymentService) -> None:
        self._payment_service = payment_service

    async def execute(self, input: DecideRefundInput, context: ExecutionContext) -> RefundOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot approve a refund — approval requires a human actor")
        try:
            refund = await self._payment_service.decide_refund(
                context.tenant_id, input.refund_id, approved=True, decided_by=context.actor_id
            )
        except InvalidRefundError as e:
            raise ValueError(str(e)) from e
        return RefundOutput(refund=_refund_to_dict(refund))


class RejectRefund(Tool):
    name = "finance.reject_refund"
    description = "Reject a requested refund."
    input_schema = DecideRefundInput
    output_schema = RefundOutput
    required_permission = Permission.APPROVE_REFUND

    def __init__(self, payment_service: PaymentService) -> None:
        self._payment_service = payment_service

    async def execute(self, input: DecideRefundInput, context: ExecutionContext) -> RefundOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot reject a refund — this requires a human actor")
        try:
            refund = await self._payment_service.decide_refund(
                context.tenant_id, input.refund_id, approved=False, decided_by=context.actor_id
            )
        except InvalidRefundError as e:
            raise ValueError(str(e)) from e
        return RefundOutput(refund=_refund_to_dict(refund))
