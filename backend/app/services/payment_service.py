"""sections 9-11: payment recording, allocation, and refunds.

Every money movement here is deterministic and Decimal-only. Idempotency
is enforced two ways: `(tenant_id, provider, external_id)` on `Payment`
(a DB unique constraint — a retried webhook/tool-call can't double-count),
and allocation always recomputes `Invoice.amount_paid`/`amount_due`/
`status` from the sum of `PaymentAllocation` rows rather than incrementing,
so re-running allocation for the same payment is a no-op, not a double-add.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.finance import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentAllocation,
    PaymentStatus,
    Refund,
    RefundStatus,
)
from app.services.approval_helper import create_approval_request


class InvoiceNotFoundError(Exception):
    pass


class PaymentNotFoundError(Exception):
    pass


class OverpaymentError(Exception):
    pass


class InvalidRefundError(Exception):
    pass


@dataclass
class AllocationInput:
    invoice_id: uuid.UUID
    amount: Decimal


class PaymentService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def record_payment(
        self,
        tenant_id: uuid.UUID,
        *,
        customer_id: uuid.UUID,
        amount: Decimal,
        provider: str,
        external_id: str,
        payment_method: str | None,
        allocations: list[AllocationInput],
    ) -> tuple[Payment, bool]:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(Payment).where(
                        Payment.tenant_id == tenant_id,
                        Payment.provider == provider,
                        Payment.external_id == external_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True

            allocated_total = sum((a.amount for a in allocations), Decimal("0"))
            if allocated_total > amount:
                raise OverpaymentError(
                    f"Allocations (${allocated_total}) exceed payment amount (${amount})"
                )

            payment = Payment(
                tenant_id=tenant_id,
                customer_id=customer_id,
                amount=amount,
                status=PaymentStatus.SUCCEEDED,
                payment_method=payment_method,
                provider=provider,
                external_id=external_id,
                received_at=datetime.now(timezone.utc),
            )
            session.add(payment)
            await session.flush()

            touched_invoice_ids: list[uuid.UUID] = []
            for alloc in allocations:
                invoice = await session.get(Invoice, alloc.invoice_id)
                if invoice is None or invoice.tenant_id != tenant_id:
                    raise InvoiceNotFoundError(f"Invoice {alloc.invoice_id} not found")
                if alloc.amount > invoice.amount_due:
                    raise OverpaymentError(
                        f"Allocation ${alloc.amount} exceeds invoice {invoice.invoice_number}'s "
                        f"amount due ${invoice.amount_due}"
                    )
                session.add(
                    PaymentAllocation(
                        tenant_id=tenant_id,
                        payment_id=payment.id,
                        invoice_id=invoice.id,
                        amount=alloc.amount,
                    )
                )
                touched_invoice_ids.append(invoice.id)

            await session.flush()

            for invoice_id in touched_invoice_ids:
                await self._recompute_invoice(session, tenant_id, invoice_id)

            await session.commit()
            await session.refresh(payment)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.PAYMENT_RECEIVED,
            source="finance",
            entity_type="payment",
            entity_id=payment.id,
            payload={"payment_id": str(payment.id), "amount": str(amount), "invoice_ids": [str(i) for i in touched_invoice_ids]},
            idempotency_key=f"payment-received-{payment.id}",
        )
        for invoice_id in touched_invoice_ids:
            async with self._session_factory() as session:
                invoice = await session.get(Invoice, invoice_id)
            if invoice and invoice.status == InvoiceStatus.PAID:
                await self._bus.publish(
                    tenant_id=tenant_id,
                    event_type=EventType.INVOICE_PAID,
                    source="finance",
                    entity_type="invoice",
                    entity_id=invoice.id,
                    payload={"invoice_id": str(invoice.id)},
                )
        return payment, False

    async def _recompute_invoice(self, session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> None:
        invoice = await session.get(Invoice, invoice_id)
        rows = (
            await session.execute(
                select(PaymentAllocation).where(
                    PaymentAllocation.tenant_id == tenant_id, PaymentAllocation.invoice_id == invoice_id
                )
            )
        ).scalars().all()
        total_paid = sum((r.amount for r in rows), Decimal("0"))
        invoice.amount_paid = total_paid
        invoice.amount_due = invoice.total - total_paid
        if invoice.amount_due <= 0:
            invoice.status = InvoiceStatus.PAID
            invoice.paid_at = datetime.now(timezone.utc)
        elif total_paid > 0:
            invoice.status = InvoiceStatus.PARTIALLY_PAID

    async def request_refund(
        self,
        tenant_id: uuid.UUID,
        *,
        payment_id: uuid.UUID,
        invoice_id: uuid.UUID | None,
        amount: Decimal,
        reason: str,
        requested_by: uuid.UUID | None,
    ) -> Refund:
        """Refunds are always APPROVAL_REQUIRED — never issued directly by AI
        or by a plain API call. See spec: 'Never allow AI to issue refunds
        directly.'"""
        async with self._session_factory() as session:
            payment = await session.get(Payment, payment_id)
            if payment is None or payment.tenant_id != tenant_id:
                raise PaymentNotFoundError("Payment not found")

            already_refunded = sum(
                (
                    r.amount
                    for r in (
                        await session.execute(
                            select(Refund).where(
                                Refund.tenant_id == tenant_id,
                                Refund.payment_id == payment_id,
                                Refund.status != RefundStatus.REJECTED,
                            )
                        )
                    ).scalars().all()
                ),
                Decimal("0"),
            )
            if already_refunded + amount > payment.amount:
                raise InvalidRefundError(
                    f"Refund total (${already_refunded + amount}) would exceed payment amount (${payment.amount})"
                )

            refund = Refund(
                tenant_id=tenant_id,
                payment_id=payment_id,
                invoice_id=invoice_id,
                amount=amount,
                reason=reason,
                status=RefundStatus.REQUESTED,
                requested_by=requested_by,
            )
            session.add(refund)
            await session.flush()

            await create_approval_request(
                session,
                tenant_id=tenant_id,
                requested_by_type="USER",
                requested_by_id=requested_by,
                tool_name="finance.approve_refund",
                action_type="refund_approval",
                reason=f"Refund of ${amount} requested: {reason}",
                tool_input={"refund_id": str(refund.id), "payment_id": str(payment_id), "amount": str(amount)},
            )

            await session.commit()
            await session.refresh(refund)
        return refund

    async def decide_refund(
        self, tenant_id: uuid.UUID, refund_id: uuid.UUID, *, approved: bool, decided_by: uuid.UUID | None
    ) -> Refund:
        from app.models.approval import ApprovalRequest, ApprovalStatus

        # Phase 12C: when the underlying payment came from Stripe, a real
        # refund must actually happen at Stripe BEFORE we ever mark our own
        # Refund row COMPLETED — never claim money moved back to the
        # customer when it didn't. Read what's needed for that call in a
        # short read-only session, make the real (possibly slow, possibly
        # failing) HTTP call OUTSIDE any open DB transaction, then commit.
        stripe_refund_needed = False
        stripe_payment_intent_id: str | None = None
        if approved:
            async with self._session_factory() as session:
                refund_preview = await session.get(Refund, refund_id)
                if refund_preview is not None and refund_preview.tenant_id == tenant_id:
                    payment_preview = await session.get(Payment, refund_preview.payment_id)
                    if payment_preview is not None and payment_preview.provider == "stripe":
                        stripe_refund_needed = True
                        stripe_payment_intent_id = payment_preview.external_id

        if stripe_refund_needed and stripe_payment_intent_id:
            from app.core.config import get_settings
            from app.integrations.stripe_client import StripeAPIError, StripeClient

            settings = get_settings()
            if not settings.STRIPE_SECRET_KEY:
                raise InvalidRefundError(
                    "Cannot complete Stripe refund: STRIPE_SECRET_KEY is not configured"
                )
            client = StripeClient(settings.STRIPE_SECRET_KEY)
            try:
                refund_amount_preview: Decimal | None = None
                async with self._session_factory() as session:
                    r = await session.get(Refund, refund_id)
                    if r is not None:
                        refund_amount_preview = r.amount
                await client.create_refund(
                    payment_intent_id=stripe_payment_intent_id,
                    amount=refund_amount_preview,
                    idempotency_key=f"klaros-refund-{refund_id}",
                )
            except StripeAPIError as exc:
                raise InvalidRefundError(f"Stripe refund failed, no DB state changed: {exc}") from exc

        async with self._session_factory() as session:
            refund = await session.get(Refund, refund_id)
            if refund is None or refund.tenant_id != tenant_id:
                raise InvalidRefundError("Refund not found")
            if refund.status != RefundStatus.REQUESTED:
                raise InvalidRefundError("Refund is not pending")

            pending = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id,
                        ApprovalRequest.tool_name == "finance.approve_refund",
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for req in pending:
                if req.tool_input.get("refund_id") == str(refund_id):
                    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    req.decided_by = decided_by

            if approved:
                refund.status = RefundStatus.COMPLETED
                refund.approved_by = decided_by

                payment = await session.get(Payment, refund.payment_id)
                payment.status = PaymentStatus.PARTIALLY_REFUNDED if refund.amount < payment.amount else PaymentStatus.REFUNDED

                if refund.invoice_id:
                    invoice = await session.get(Invoice, refund.invoice_id)
                    if invoice is not None:
                        invoice.amount_paid -= refund.amount
                        invoice.amount_due += refund.amount
                        if invoice.status == InvoiceStatus.PAID:
                            invoice.status = InvoiceStatus.PARTIALLY_PAID
                        invoice.paid_at = None
            else:
                refund.status = RefundStatus.REJECTED

            await session.commit()
            await session.refresh(refund)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.PAYMENT_REFUNDED if approved else EventType.PAYMENT_FAILED,
            source="finance",
            entity_type="refund",
            entity_id=refund.id,
            payload={"refund_id": str(refund.id), "approved": approved},
        )
        return refund

    async def reconcile_external_refund(
        self,
        tenant_id: uuid.UUID,
        *,
        provider: str,
        external_payment_id: str,
        total_amount_refunded: Decimal,
        reason: str,
    ) -> tuple[Refund | None, str | None]:
        """Phase 12F: reconciles a refund issued OUTSIDE Klaros (e.g. via
        the Stripe Dashboard, or Stripe's own dispute process) against the
        matching `Payment` — distinct from `decide_refund`, which is the
        path for a refund Klaros itself REQUESTED and approved. Both paths
        converge on the same `Payment`/`Invoice` state, just from opposite
        directions (Klaros-initiated -> real Stripe call -> DB update, vs.
        Stripe-initiated -> DB reconciliation only, no outbound call).

        `total_amount_refunded` is the provider's own CUMULATIVE total (not
        a delta) — mirrors `record_payment`'s "recompute from the real
        source of truth, never increment" idempotency pattern: re-delivery
        of the same cumulative total is a safe no-op, not a double-refund.
        Returns (None, None) for that legitimate no-op case, and
        (None, error_detail) for a genuine failure (no matching Payment)."""
        async with self._session_factory() as session:
            payment = (
                await session.execute(
                    select(Payment).where(
                        Payment.tenant_id == tenant_id,
                        Payment.provider == provider,
                        Payment.external_id == external_payment_id,
                    )
                )
            ).scalar_one_or_none()
            if payment is None:
                return None, f"No matching Payment found for {provider} payment {external_payment_id}"

            already_refunded = sum(
                (
                    r.amount
                    for r in (
                        await session.execute(
                            select(Refund).where(
                                Refund.tenant_id == tenant_id,
                                Refund.payment_id == payment.id,
                                Refund.status == RefundStatus.COMPLETED,
                            )
                        )
                    ).scalars().all()
                ),
                Decimal("0"),
            )
            delta = total_amount_refunded - already_refunded
            if delta <= 0:
                # Already reconciled to this (or a higher) total — a
                # redelivered/duplicate webhook for the same cumulative
                # amount is a safe no-op, not an error.
                return None, None

            allocation = (
                await session.execute(
                    select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
                )
            ).scalars().first()

            refund = Refund(
                tenant_id=tenant_id,
                payment_id=payment.id,
                invoice_id=allocation.invoice_id if allocation else None,
                amount=delta,
                reason=reason,
                status=RefundStatus.COMPLETED,
            )
            session.add(refund)
            await session.flush()

            new_total_refunded = already_refunded + delta
            payment.status = (
                PaymentStatus.PARTIALLY_REFUNDED if new_total_refunded < payment.amount else PaymentStatus.REFUNDED
            )

            if allocation is not None:
                invoice = await session.get(Invoice, allocation.invoice_id)
                if invoice is not None:
                    invoice.amount_paid -= delta
                    invoice.amount_due += delta
                    if invoice.status == InvoiceStatus.PAID:
                        invoice.status = InvoiceStatus.PARTIALLY_PAID
                    invoice.paid_at = None

            await session.commit()
            await session.refresh(refund)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.PAYMENT_REFUNDED,
            source=f"{provider}_webhook",
            entity_type="refund",
            entity_id=refund.id,
            payload={"refund_id": str(refund.id), "external_payment_id": external_payment_id, "reconciled": True},
        )
        return refund, None
