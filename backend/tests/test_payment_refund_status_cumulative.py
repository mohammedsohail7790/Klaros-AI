"""Phase 20: a real bug found during the accounting-lifecycle audit —
`PaymentService.decide_refund` set `Payment.status` by comparing THIS
refund's own amount against `Payment.amount`, not the CUMULATIVE total
refunded against that payment. A payment fully refunded via several
partial refunds (each individually less than the full amount) therefore
never reached `PaymentStatus.REFUNDED`, staying `PARTIALLY_REFUNDED`
forever — inconsistent with `PaymentService.reconcile_external_refund`,
which already used the correct cumulative comparison. Fixed to match.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.integrations.stripe_client import StripeClient
from app.integrations.stripe_schemas import StripeRefundResponse
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentAllocation, PaymentStatus, Refund
from app.services.payment_service import PaymentService

pytestmark = pytest.mark.asyncio


async def _make_paid_invoice_and_payment(tenant_id: uuid.UUID, *, amount: Decimal = Decimal("100.00")):
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Cumulative Refund Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"CUM-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.PAID, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=amount, total=amount, amount_paid=amount, amount_due=Decimal("0.00"),
        )
        session.add(invoice)
        await session.flush()
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=amount, status=PaymentStatus.SUCCEEDED,
            provider="stripe", external_id=f"pi_{uuid.uuid4().hex}", received_at=invoice.created_at,
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=amount))
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(payment)
    return invoice, payment


@pytest.fixture(autouse=True)
def _configure_stripe_key(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    yield


@pytest.fixture(autouse=True)
def _fake_stripe_refund(monkeypatch):
    async def _fake_create_refund(self, *, payment_intent_id, amount=None, reason=None, idempotency_key=None):
        return StripeRefundResponse(
            id=f"re_{uuid.uuid4().hex}", status="succeeded", amount=int((amount or 0) * 100), currency="usd",
        )

    monkeypatch.setattr(StripeClient, "create_refund", _fake_create_refund)
    yield


async def test_two_partial_refunds_summing_to_full_amount_mark_payment_refunded(event_bus) -> None:
    tenant_id = uuid.uuid4()
    _invoice, payment = await _make_paid_invoice_and_payment(tenant_id, amount=Decimal("100.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    refund_1 = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("40.00"),
        reason="Partial 1", requested_by=None,
    )
    refund_1 = await service.decide_refund(tenant_id, refund_1.id, approved=True, decided_by=None)
    assert refund_1.status == "COMPLETED"

    async with async_session_maker() as session:
        after_first = await session.get(Payment, payment.id)
        assert after_first.status == PaymentStatus.PARTIALLY_REFUNDED

    refund_2 = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("60.00"),
        reason="Partial 2 — completes the refund", requested_by=None,
    )
    refund_2 = await service.decide_refund(tenant_id, refund_2.id, approved=True, decided_by=None)
    assert refund_2.status == "COMPLETED"

    async with async_session_maker() as session:
        after_second = await session.get(Payment, payment.id)
        # Before the fix: refund_2.amount (60.00) < payment.amount
        # (100.00) alone, so status would have stayed PARTIALLY_REFUNDED
        # even though 40 + 60 = 100 has now genuinely been refunded in
        # full. The fix compares the CUMULATIVE total, matching
        # reconcile_external_refund's already-correct behavior.
        assert after_second.status == PaymentStatus.REFUNDED


async def test_three_partial_refunds_none_individually_reaching_full_amount(event_bus) -> None:
    """A sharper reproduction of the bug: three refunds of ~34% each,
    none of which is ever, by itself, close to the full amount — the old
    code's `refund.amount < payment.amount` check would be trivially
    true for every single one of them."""
    tenant_id = uuid.uuid4()
    _invoice, payment = await _make_paid_invoice_and_payment(tenant_id, amount=Decimal("90.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    for amount in (Decimal("30.00"), Decimal("30.00"), Decimal("30.00")):
        refund = await service.request_refund(
            tenant_id, payment_id=payment.id, invoice_id=None, amount=amount,
            reason="Equal thirds", requested_by=None,
        )
        await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)

    async with async_session_maker() as session:
        final_payment = await session.get(Payment, payment.id)
        assert final_payment.status == PaymentStatus.REFUNDED

        total_refunded = sum(
            (
                r.amount
                for r in (
                    await session.execute(select(Refund).where(Refund.payment_id == payment.id))
                ).scalars().all()
            ),
            Decimal("0"),
        )
        assert total_refunded == Decimal("90.00")


async def test_single_partial_refund_still_marks_partially_refunded(event_bus) -> None:
    """Regression: the fix must not overcorrect — a single refund that
    genuinely doesn't cover the full amount must still leave the payment
    PARTIALLY_REFUNDED, not jump straight to REFUNDED."""
    tenant_id = uuid.uuid4()
    _invoice, payment = await _make_paid_invoice_and_payment(tenant_id, amount=Decimal("100.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    refund = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("25.00"),
        reason="Small partial", requested_by=None,
    )
    await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.status == PaymentStatus.PARTIALLY_REFUNDED


async def test_single_full_refund_still_marks_refunded_directly(event_bus) -> None:
    """Regression: the simple, already-correct case (one refund for the
    full amount) must be unaffected by the fix."""
    tenant_id = uuid.uuid4()
    _invoice, payment = await _make_paid_invoice_and_payment(tenant_id, amount=Decimal("50.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    refund = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("50.00"),
        reason="Full refund", requested_by=None,
    )
    await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=None)

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.status == PaymentStatus.REFUNDED


async def test_rejected_refund_never_counted_toward_cumulative_total(event_bus) -> None:
    """A REJECTED refund must never contribute to the cumulative total —
    otherwise a rejected-then-re-requested-and-approved refund could
    incorrectly flip status to REFUNDED early."""
    tenant_id = uuid.uuid4()
    _invoice, payment = await _make_paid_invoice_and_payment(tenant_id, amount=Decimal("100.00"))
    from app.api.tool_deps_integrations import get_integration_connection_service
    service = PaymentService(async_session_maker, event_bus, get_integration_connection_service())

    refund_rejected = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("70.00"),
        reason="Will be rejected", requested_by=None,
    )
    await service.decide_refund(tenant_id, refund_rejected.id, approved=False, decided_by=None)

    refund_approved = await service.request_refund(
        tenant_id, payment_id=payment.id, invoice_id=None, amount=Decimal("40.00"),
        reason="Genuinely approved", requested_by=None,
    )
    await service.decide_refund(tenant_id, refund_approved.id, approved=True, decided_by=None)

    async with async_session_maker() as session:
        refreshed = await session.get(Payment, payment.id)
        # 40 out of 100 refunded (the 70 was rejected, never happened) —
        # must be PARTIALLY_REFUNDED, not REFUNDED.
        assert refreshed.status == PaymentStatus.PARTIALLY_REFUNDED
