"""Phase 23: a real, reliably-reproduced concurrency bug in
`PaymentService.request_refund`, found while auditing STEP 5's concurrency
battery ("two concurrent partial refunds") and STEP 4's cumulative-refund
accounting requirement ("the system must never accidentally refund more
than the original payment").

Distinct from every previously-fixed refund race in this codebase: Phase
21's "concurrent refund approval" bug was about two calls deciding the SAME
refund_id twice (fixed with a CAS claim in `decide_refund`). This is a
DIFFERENT bug — two (or more) *separate* refund REQUESTS against the SAME
payment, each individually within the payment's remaining refundable
amount at the moment it was checked, submitted concurrently (e.g. two
support agents acting on the same payment at the same time). Each
`request_refund` call read `already_refunded` (the sum of all non-REJECTED
refunds against the payment) before any of the others committed, so all of
them independently passed the "does this fit?" check.

Reproduced directly (not inferred) under `asyncio.gather` against real
PostgreSQL: 5 concurrent $30 refund requests against a single $100 payment
ALL succeeded, producing $150 in REQUESTED refunds against a $100 payment
— a genuine overcommitment. Refunds are never auto-approved (a human
always decides `decide_refund` separately), so this alone does not
directly move money, but nothing elsewhere in this codebase re-validates
the cumulative total against the payment amount at approval time — so a
support agent approving more than one of these pending requests (a
realistic scenario in a busy refund queue, where nothing on screen
necessarily surfaces the other pending request) was a real path to Stripe
being asked to refund more than the original payment.

Fixed with `with_for_update=True` on the Payment row load in
`request_refund` — mirrors the exact fix Phase 21 already used for
concurrent invoice overpayment in `record_payment`'s allocation path. Only
verified against real PostgreSQL (SQLite has no row-level locking and
silently ignores the hint, per this project's established pattern for
this class of fix)."""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker, engine
from app.models.crm import Customer
from app.models.finance import Payment, PaymentStatus, Refund
from app.services.payment_service import InvalidRefundError, PaymentService

pytestmark = pytest.mark.asyncio


async def _make_payment(tenant_id: uuid.UUID, *, amount: Decimal = Decimal("100.00")) -> Payment:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Phase23 Refund Race Customer")
        session.add(customer)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=amount,
            status=PaymentStatus.SUCCEEDED, provider="internal_test", external_id=f"itp_{uuid.uuid4().hex}",
            received_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
    return payment


async def test_concurrent_refund_requests_never_exceed_the_payment_amount(event_bus) -> None:
    if engine.dialect.name == "sqlite":
        # SQLite's single shared StaticPool connection cannot hold multiple
        # truly overlapping transactions for this interleaving — see the
        # module docstring and this project's established pattern (Phase 21
        # `with_for_update` fix, Phase 22 concurrent-Job-creation fix) for
        # why this assertion is only meaningful against real Postgres.
        return

    tenant_id = uuid.uuid4()
    payment = await _make_payment(tenant_id, amount=Decimal("100.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    results = await asyncio.gather(
        *[
            service.request_refund(
                tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("30.00"),
                reason=f"concurrent request {i}", requested_by=None,
            )
            for i in range(5)
        ],
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 3, f"expected exactly 3 of 5 $30 requests to fit within a $100 payment: {results}"
    assert all(isinstance(e, InvalidRefundError) for e in failures)

    async with async_session_maker() as session:
        refunds = (await session.execute(select(Refund).where(Refund.payment_id == payment.id))).scalars().all()
        total_requested = sum((r.amount for r in refunds), Decimal("0"))
        assert len(refunds) == 3
        assert total_requested == Decimal("90.00")
        assert total_requested <= payment.amount, "refund requests must never collectively exceed the payment amount"


async def test_sequential_refund_requests_within_bounds_still_work(event_bus) -> None:
    """The fix must not break the ordinary, non-concurrent case: two
    legitimate sequential partial-refund requests that together fit."""
    tenant_id = uuid.uuid4()
    payment = await _make_payment(tenant_id, amount=Decimal("100.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    r1 = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("40.00"), reason="first", requested_by=None,
    )
    r2 = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("60.00"), reason="second", requested_by=None,
    )
    assert r1.amount == Decimal("40.00")
    assert r2.amount == Decimal("60.00")

    with pytest.raises(InvalidRefundError, match="would exceed payment amount"):
        await service.request_refund(
            tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("0.01"), reason="third", requested_by=None,
        )
