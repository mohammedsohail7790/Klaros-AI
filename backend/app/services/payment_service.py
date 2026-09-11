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
        quote_id: uuid.UUID | None = None,
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
                # Phase 29 fix: do NOT return here without publishing.
                # Previously this short-circuited straight back to the
                # caller, meaning if a PRIOR call's own DB commit for this
                # exact Payment succeeded but its later `self._bus.publish`
                # call then failed (e.g. a transient Redis/transport
                # outage) — a real, reproduced scenario, not theoretical —
                # `PAYMENT_RECEIVED` was never published, and a retry
                # (which any caller reasonably performs after catching that
                # exception) would land HERE, on this dedup path, and
                # return immediately without ever attempting the publish
                # again. The event was then permanently, silently lost —
                # no downstream QuickBooks sync, no notification, nothing
                # — with no error surfaced after that first failed
                # attempt. Falling through to the same publish call below
                # (reusing the deterministic
                # `payment-received-{payment.id}` idempotency key) closes
                # this: `EventBus.publish` already deduplicates on that key
                # (see `app/events/bus.py`), so calling it again when the
                # event genuinely was already published is a cheap, safe
                # no-op — but when it wasn't, this is now the retry path
                # that actually delivers it.
                touched_invoice_ids = [
                    a.invoice_id
                    for a in (
                        await session.execute(
                            select(PaymentAllocation).where(PaymentAllocation.payment_id == existing.id)
                        )
                    ).scalars().all()
                ]
                await self._publish_payment_received(tenant_id, existing, touched_invoice_ids)
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
                quote_id=quote_id,
            )
            session.add(payment)
            await session.flush()

            # Phase 21 fix: track running-allocated-so-far PER INVOICE
            # within this single call, not just each allocation checked
            # independently against the invoice's own (unchanged-until-
            # the-loop-ends) `amount_due`. Without this, two allocations
            # to the SAME invoice in one `record_payment` call (e.g. a
            # caller-supplied duplicate, or a legitimate "top up the same
            # invoice twice in one payment" request) each individually
            # pass the `alloc.amount > invoice.amount_due` check against
            # the SAME starting amount_due, together silently overpaying
            # the invoice — confirmed by direct reproduction: two $60
            # allocations against a $100-due invoice each pass individually
            # (60 <= 100) but sum to $120, driving amount_due negative.
            # Phase 21 fix (second, independent race found on the same
            # audit pass): TWO SEPARATE `record_payment` calls allocating
            # to the SAME invoice concurrently (e.g. a customer
            # double-paying via two browser tabs, each producing its own
            # real Stripe payment) previously each read the invoice's
            # `amount_due` in their own transaction before either
            # committed, so both could independently pass the overpayment
            # check — confirmed by direct reproduction under
            # `asyncio.gather`: two concurrent $60 payments against a
            # $100-due invoice both succeeded, driving amount_due to
            # -$20.00. `with_for_update=True` takes a real row lock on
            # Postgres (production), forcing the second transaction to
            # wait for the first to commit and see its updated
            # `amount_due` before validating — closing the race for real.
            # SQLite (this project's test/dev default) has no row-level
            # locking and silently ignores the hint, so this specific race
            # is only closed against real Postgres — verified there, not
            # merely assumed, per this phase's own audit standard.
            allocated_so_far: dict[uuid.UUID, Decimal] = {}
            touched_invoice_ids: list[uuid.UUID] = []
            for alloc in allocations:
                invoice = await session.get(Invoice, alloc.invoice_id, with_for_update=True)
                if invoice is None or invoice.tenant_id != tenant_id:
                    raise InvoiceNotFoundError(f"Invoice {alloc.invoice_id} not found")
                already_allocated_this_call = allocated_so_far.get(invoice.id, Decimal("0"))
                if already_allocated_this_call + alloc.amount > invoice.amount_due:
                    raise OverpaymentError(
                        f"Allocation ${alloc.amount} exceeds invoice {invoice.invoice_number}'s "
                        f"remaining amount due ${invoice.amount_due - already_allocated_this_call} "
                        f"(${already_allocated_this_call} already allocated to it earlier in this same payment)"
                    )
                allocated_so_far[invoice.id] = already_allocated_this_call + alloc.amount
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

        await self._publish_payment_received(tenant_id, payment, touched_invoice_ids)
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

    async def _publish_payment_received(
        self, tenant_id: uuid.UUID, payment: Payment, touched_invoice_ids: list[uuid.UUID]
    ) -> None:
        """Phase 29 fix: factored out so BOTH the fresh-creation path and
        the dedup (already-exists) path in `record_payment` reach the same
        publish call, with the same deterministic `payment-received-
        {payment.id}` idempotency key. `EventBus.publish` already
        deduplicates on that key, so calling this again for a payment
        whose event genuinely was already published is a safe no-op —
        closing the window where a publish failure right after a
        successful commit could permanently, silently lose the event
        (confirmed by direct reproduction: `EventBus.publish` monkeypatched
        to fail once, Payment still committed, but a subsequent retry via
        the dedup path previously never re-attempted the publish at all)."""
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.PAYMENT_RECEIVED,
            source="finance",
            entity_type="payment",
            entity_id=payment.id,
            payload={
                "payment_id": str(payment.id), "amount": str(payment.amount),
                "invoice_ids": [str(i) for i in touched_invoice_ids],
            },
            idempotency_key=f"payment-received-{payment.id}",
        )

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
        directly.'

        Phase 23 fix: `with_for_update=True` takes a real Postgres row lock
        on the Payment for the duration of this check-then-insert — mirrors
        the exact fix already used for concurrent invoice overpayment
        (Phase 21, `record_payment`'s allocation path). Without it, two
        concurrent refund REQUESTS against the same payment (e.g. two
        support agents each requesting a partial refund at the same time)
        could each read the same `already_refunded` total before either
        commits, both pass the "does this fit within the payment amount?"
        check, and both insert a REQUESTED refund — reproduced directly
        under `asyncio.gather` against real PostgreSQL: 5 concurrent $30
        requests against a $100 payment all succeeded, totalling $150 in
        REQUESTED refunds. Requests are never auto-approved (a human always
        decides), but nothing else in this codebase re-validates the
        cumulative total against the payment amount at approval time, so
        this was a real path to a genuine over-refund if an approver acted
        on more than one of them. SQLite has no row-level locking and
        silently ignores this hint — this fix is verified against real
        Postgres specifically, matching the project's established pattern."""
        async with self._session_factory() as session:
            payment = await session.get(Payment, payment_id, with_for_update=True)
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
        from sqlalchemy import update as sa_update

        # Phase 12C: when the underlying payment came from Stripe, a real
        # refund must actually happen at Stripe BEFORE we ever mark our own
        # Refund row COMPLETED — never claim money moved back to the
        # customer when it didn't. Read what's needed for that call in a
        # short read-only session, make the real (possibly slow, possibly
        # failing) HTTP call OUTSIDE any open DB transaction, then commit.
        #
        # Phase 21 fix: a plain "read status, act later" check here is a
        # real, reproduced race — two concurrent `decide_refund(approved=
        # True)` calls for the SAME refund could both read REQUESTED
        # before either wrote back, and both proceed to call Stripe
        # (confirmed directly: two real `create_refund` calls fired for
        # one refund under `asyncio.gather`). The only thing that made
        # this safe was relying entirely on STRIPE'S OWN idempotency key
        # to collapse the two calls into one real refund — genuinely true
        # per Stripe's documented contract, but not something this app can
        # verify (no credentials) and not a substitute for Klaros' own
        # concurrency safety. Closed by atomically CAS-claiming the refund
        # (REQUESTED -> APPROVED, a status previously defined but never
        # assigned — see the Phase 12G finding) via a conditional UPDATE
        # before ever deciding whether to call Stripe: only the caller
        # whose UPDATE actually matches one row proceeds; the other sees
        # 0 rows affected and raises immediately, before touching Stripe.
        # A Stripe failure reverts the claim back to REQUESTED so the
        # refund remains retryable, matching the pre-existing "no DB state
        # changed on failure" guarantee.
        stripe_refund_needed = False
        stripe_payment_intent_id: str | None = None
        if approved:
            async with self._session_factory() as session:
                claim_result = await session.execute(
                    sa_update(Refund)
                    .where(
                        Refund.id == refund_id, Refund.tenant_id == tenant_id,
                        Refund.status == RefundStatus.REQUESTED,
                    )
                    .values(status=RefundStatus.APPROVED)
                )
                await session.commit()
                claimed = claim_result.rowcount == 1

            if not claimed:
                async with self._session_factory() as session:
                    existing = await session.get(Refund, refund_id)
                if existing is None or existing.tenant_id != tenant_id:
                    raise InvalidRefundError("Refund not found")
                raise InvalidRefundError("Refund is not pending")

            async with self._session_factory() as session:
                refund_preview = await session.get(Refund, refund_id)
                payment_preview = await session.get(Payment, refund_preview.payment_id)
                if payment_preview is not None and payment_preview.provider == "stripe":
                    stripe_refund_needed = True
                    stripe_payment_intent_id = payment_preview.external_id

        if stripe_refund_needed and stripe_payment_intent_id:
            from app.core.config import get_settings
            from app.integrations.stripe_client import StripeAPIError, StripeClient

            settings = get_settings()
            if not settings.STRIPE_SECRET_KEY:
                async with self._session_factory() as session:
                    r = await session.get(Refund, refund_id)
                    if r is not None and r.status == RefundStatus.APPROVED:
                        r.status = RefundStatus.REQUESTED
                        await session.commit()
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
                # Revert the claim so a genuinely-failed attempt remains
                # retryable — never leave the refund stuck in APPROVED
                # with no real refund behind it.
                async with self._session_factory() as session:
                    r = await session.get(Refund, refund_id)
                    if r is not None and r.status == RefundStatus.APPROVED:
                        r.status = RefundStatus.REQUESTED
                        await session.commit()
                raise InvalidRefundError(f"Stripe refund failed, no DB state changed: {exc}") from exc

        async with self._session_factory() as session:
            refund = await session.get(Refund, refund_id)
            if refund is None or refund.tenant_id != tenant_id:
                raise InvalidRefundError("Refund not found")
            # Phase 21: the approved path already atomically claimed the
            # refund into APPROVED above (skipped entirely for the
            # rejected path, which never touches an external provider and
            # keeps the original, lower-stakes check-then-act guard).
            expected_status = RefundStatus.APPROVED if approved else RefundStatus.REQUESTED
            if refund.status != expected_status:
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
                # Phase 20 fix: must compare the CUMULATIVE total refunded
                # against this payment (including this refund), not just
                # this one refund's own amount — a payment fully refunded
                # via several partial refunds (e.g. two 50% refunds
                # decided separately) previously never reached REFUNDED,
                # since each individual refund.amount was < payment.amount
                # even though their sum equalled it. Mirrors
                # reconcile_external_refund's already-correct cumulative
                # comparison below, which this path should have matched
                # from the start.
                total_refunded = sum(
                    (
                        r.amount
                        for r in (
                            await session.execute(
                                select(Refund).where(
                                    Refund.tenant_id == tenant_id,
                                    Refund.payment_id == refund.payment_id,
                                    Refund.status == RefundStatus.COMPLETED,
                                )
                            )
                        ).scalars().all()
                    ),
                    Decimal("0"),
                )
                payment.status = (
                    PaymentStatus.PARTIALLY_REFUNDED if total_refunded < payment.amount else PaymentStatus.REFUNDED
                )

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
