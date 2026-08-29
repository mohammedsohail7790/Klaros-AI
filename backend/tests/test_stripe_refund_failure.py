"""Phase 12F Step 17: a real Stripe refund API failure must leave NO DB
state changed — the refund stays REQUESTED, the payment/invoice are
untouched — proven directly against PaymentService.decide_refund with the
real Stripe HTTP call mocked to fail (StripeClient.create_refund raising),
not by inspecting the code."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.actor import ActorType
from app.models.finance import Invoice, InvoiceStatus, Payment, PaymentStatus, Refund, RefundStatus
from app.models.rbac import Role
from app.services.payment_service import InvalidRefundError
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _make_stripe_paid_invoice(tool_registry, tenant_id: uuid.UUID):
    """A real invoice paid via a REAL (fixture) Stripe payment_intent.succeeded
    flow, matching the shape refund tests need: Payment.provider == "stripe"."""
    from app.db.session import async_session_maker
    from app.models.crm import Customer
    from app.models.finance import PaymentAllocation

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Refund Failure Test Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id,
            invoice_number=f"RFFAIL-{uuid.uuid4().hex[:8]}", status=InvoiceStatus.PAID,
            issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"), total=Decimal("100.00"),
            amount_paid=Decimal("100.00"), amount_due=Decimal("0.00"),
        )
        session.add(invoice)
        await session.flush()

        payment_intent_id = f"pi_{uuid.uuid4().hex}"
        payment = Payment(
            tenant_id=tenant_id, customer_id=customer.id, amount=Decimal("100.00"),
            status=PaymentStatus.SUCCEEDED, provider="stripe", external_id=payment_intent_id,
            received_at=invoice.created_at,
        )
        session.add(payment)
        await session.flush()
        session.add(PaymentAllocation(tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id, amount=Decimal("100.00")))
        await session.commit()
        await session.refresh(invoice)
        await session.refresh(payment)

    from app.services.approval_helper import create_approval_request

    async with async_session_maker() as session:
        refund = Refund(
            tenant_id=tenant_id, payment_id=payment.id, invoice_id=invoice.id,
            amount=Decimal("40.00"), reason="test", status=RefundStatus.REQUESTED,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)

    return invoice, payment, refund


async def test_stripe_refund_api_failure_leaves_no_db_state_changed(monkeypatch, tool_registry) -> None:
    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus
    from app.integrations.stripe_client import StripeAPIError, StripeClient
    from app.services.payment_service import PaymentService

    tenant_id = uuid.uuid4()
    invoice, payment, refund = await _make_stripe_paid_invoice(tool_registry, tenant_id)

    async def _failing_create_refund(self, **kwargs):
        raise StripeAPIError("card_declined: refund could not be processed", status_code=402)

    monkeypatch.setattr(StripeClient, "create_refund", _failing_create_refund)

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake_for_refund_failure")

    service = PaymentService(async_session_maker, get_event_bus())
    with pytest.raises(InvalidRefundError, match="Stripe refund failed, no DB state changed"):
        await service.decide_refund(tenant_id, refund.id, approved=True, decided_by=uuid.uuid4())

    async with async_session_maker() as session:
        refreshed_refund = await session.get(Refund, refund.id)
        refreshed_payment = await session.get(Payment, payment.id)
        refreshed_invoice = await session.get(Invoice, invoice.id)

        # Still exactly as it was before the failed attempt — nothing
        # partially applied.
        assert refreshed_refund.status == RefundStatus.REQUESTED
        assert refreshed_payment.status == PaymentStatus.SUCCEEDED
        assert refreshed_invoice.amount_paid == Decimal("100.00")
        assert refreshed_invoice.amount_due == Decimal("0.00")
        assert refreshed_invoice.status == InvoiceStatus.PAID
