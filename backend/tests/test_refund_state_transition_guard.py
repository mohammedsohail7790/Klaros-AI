"""Gap identified in the Phase 12F Stripe audit: `PaymentService.decide_refund`
guards against re-deciding a refund with a single hand-coded check
(`refund.status != RefundStatus.REQUESTED`), not a general state-machine
validator. These tests prove that one guard actually covers the full
transition table for a non-Stripe payment (no external HTTP call involved):
REQUESTED -> COMPLETED/REJECTED is allowed exactly once; deciding an
already-COMPLETED or already-REJECTED refund again is always rejected, and
leaves every DB row untouched."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentStatus, Refund, RefundStatus
from app.services.payment_service import InvalidRefundError, PaymentService

pytestmark = pytest.mark.asyncio


async def _make_paid_invoice_with_refund(tenant_id: uuid.UUID):
    from app.db.session import async_session_maker
    from app.models.crm import Customer
    from app.models.finance import PaymentAllocation

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Refund Guard Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id,
            invoice_number=f"RGUARD-{uuid.uuid4().hex[:8]}", status=InvoiceStatus.PAID,
            issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"), total=Decimal("100.00"),
            amount_paid=Decimal("100.00"), amount_due=Decimal("0.00"),
        )
        session.add(invoice)
        await session.flush()

        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"),
            status=PaymentStatus.SUCCEEDED, provider="internal_test", external_id=f"itp_{uuid.uuid4().hex}",
            received_at=invoice.created_at,
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("100.00")))

        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id,
            amount=Decimal("40.00"), reason="test", status=RefundStatus.REQUESTED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(payment)
        await session.refresh(refund)

    return invoice, payment, refund


async def _service() -> PaymentService:
    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus

    return PaymentService(async_session_maker, get_event_bus(), get_integration_connection_service())


async def test_deciding_an_already_completed_refund_again_is_rejected() -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, payment, refund = await _make_paid_invoice_with_refund(tenant_id)
    service = await _service()

    await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=uuid.uuid4())

    async with async_session_maker() as session:
        completed = await session.get(Refund, refund.id)
        completed_payment = await session.get(Payment, payment.id)
        completed_invoice = await session.get(Invoice, invoice.id)
        assert completed.status == RefundStatus.COMPLETED
        assert completed_payment.status == PaymentStatus.PARTIALLY_REFUNDED
        assert completed_invoice.amount_paid == Decimal("60.00")

    with pytest.raises(InvalidRefundError, match="Refund is not pending"):
        await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=uuid.uuid4())
    with pytest.raises(InvalidRefundError, match="Refund is not pending"):
        await service.decide_refund(tenant_id, refund.id, approved=False, decided_by=uuid.uuid4())

    async with async_session_maker() as session:
        still = await session.get(Refund, refund.id)
        still_payment = await session.get(Payment, payment.id)
        still_invoice = await session.get(Invoice, invoice.id)
        # A second decision must not touch anything already settled.
        assert still.status == RefundStatus.COMPLETED
        assert still_payment.status == PaymentStatus.PARTIALLY_REFUNDED
        assert still_invoice.amount_paid == Decimal("60.00")
        assert still_invoice.amount_due == Decimal("40.00")


async def test_deciding_an_already_rejected_refund_again_is_rejected() -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    invoice, payment, refund = await _make_paid_invoice_with_refund(tenant_id)
    service = await _service()

    await service.decide_refund(tenant_id, refund.id, approved=False, decided_by=uuid.uuid4())

    async with async_session_maker() as session:
        rejected = await session.get(Refund, refund.id)
        assert rejected.status == RefundStatus.REJECTED

    with pytest.raises(InvalidRefundError, match="Refund is not pending"):
        await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=uuid.uuid4())

    async with async_session_maker() as session:
        still_rejected = await session.get(Refund, refund.id)
        still_payment = await session.get(Payment, payment.id)
        still_invoice = await session.get(Invoice, invoice.id)
        # Approving a rejected refund after the fact must never move money.
        assert still_rejected.status == RefundStatus.REJECTED
        assert still_payment.status == PaymentStatus.SUCCEEDED
        assert still_invoice.amount_paid == Decimal("100.00")
        assert still_invoice.amount_due == Decimal("0.00")
