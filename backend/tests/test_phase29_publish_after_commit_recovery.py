"""Phase 29: a real, reproduced defect in the boundary between a
`PaymentService.record_payment` DB commit and its subsequent
`EventBus.publish` call — a new axis distinct from every prior phase's
concurrency/idempotency findings (this is about a SEQUENTIAL crash window
between two separate operations in the same call, not a race between two
callers).

`record_payment` commits the new `Payment` row inside its own session,
closes that session, and only THEN calls `self._bus.publish(...)` for
`PAYMENT_RECEIVED` as a completely separate operation. If that publish call
fails for any reason (a transient Redis/transport outage, a DB hiccup
inside `EventBus.publish`'s own session) — a realistic, not theoretical,
failure mode — the exception propagates out of `record_payment` even
though the `Payment` itself is durably committed. The caller (the Stripe
webhook handler) reasonably retries. But the retry lands on
`record_payment`'s own idempotency short-circuit (`existing is not None`),
which previously returned immediately WITHOUT ever attempting the publish
again — permanently, silently losing `PAYMENT_RECEIVED` for that payment,
with no error surfaced after the first failed attempt. Any downstream
consumer (QuickBooks payment sync, notifications) would simply never fire
for that payment.

Fixed by factoring the publish call into `_publish_payment_received` and
calling it from BOTH the dedup path and the fresh-creation path, relying
on `EventBus.publish`'s own existing idempotency-key dedup
(`payment-received-{payment.id}`) to make the dedup path's call a safe
no-op when the event genuinely was already published, and a genuine
recovery when it wasn't."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.crm import Customer
from app.models.event import Event
from app.models.finance import Invoice, InvoiceStatus, Payment
from app.services.payment_service import AllocationInput, PaymentService

pytestmark = pytest.mark.asyncio


async def _make_customer_and_invoice(tenant_id: uuid.UUID) -> tuple[Customer, Invoice]:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Phase29 Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"P29-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("100.00"), total=Decimal("100.00"), amount_due=Decimal("100.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(customer)
        await session.refresh(invoice)
    return customer, invoice


async def test_payment_received_event_recovers_after_a_publish_failure_and_retry(
    event_bus: EventBus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    service = PaymentService(async_session_maker, event_bus)

    original_publish = EventBus.publish

    async def _failing_publish(self, *args, **kwargs):
        raise ConnectionError("simulated transient Redis/transport outage during publish")

    monkeypatch.setattr(EventBus, "publish", _failing_publish)

    with pytest.raises(ConnectionError):
        await service.record_payment(
            tenant_id, customer_id=customer.id, amount=Decimal("100.00"), provider="stripe",
            external_id="pi_phase29_repro", payment_method="card",
            allocations=[AllocationInput(invoice_id=invoice.id, amount=Decimal("100.00"))],
        )

    async with async_session_maker() as session:
        payments = (await session.execute(select(Payment).where(Payment.tenant_id == tenant_id))).scalars().all()
        assert len(payments) == 1, "the Payment must be durably committed despite the publish failure"

        events = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "payment.received"))
        ).scalars().all()
        assert len(events) == 0, "the event genuinely was never published on the first attempt"

    # The transient issue clears; the caller retries with the SAME external_id
    # (exactly what a redelivered Stripe webhook does).
    monkeypatch.setattr(EventBus, "publish", original_publish)

    payment2, deduped = await service.record_payment(
        tenant_id, customer_id=customer.id, amount=Decimal("100.00"), provider="stripe",
        external_id="pi_phase29_repro", payment_method="card",
        allocations=[AllocationInput(invoice_id=invoice.id, amount=Decimal("100.00"))],
    )
    assert deduped is True
    assert payment2.id == payments[0].id

    async with async_session_maker() as session:
        payments_after = (
            await session.execute(select(Payment).where(Payment.tenant_id == tenant_id))
        ).scalars().all()
        assert len(payments_after) == 1, "the retry must never create a second Payment"

        events_after = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "payment.received"))
        ).scalars().all()
        assert len(events_after) == 1, "the retry must now genuinely publish the previously-lost event"
        assert events_after[0].payload["payment_id"] == str(payment2.id)


async def test_a_second_dedup_retry_after_genuine_success_does_not_duplicate_the_event(
    event_bus: EventBus,
) -> None:
    """Once the event genuinely was published, a further dedup-path call
    (e.g. yet another redelivered webhook) must be a safe no-op, not a
    second Event row."""
    tenant_id = uuid.uuid4()
    customer, invoice = await _make_customer_and_invoice(tenant_id)
    service = PaymentService(async_session_maker, event_bus)

    payment1, deduped1 = await service.record_payment(
        tenant_id, customer_id=customer.id, amount=Decimal("100.00"), provider="stripe",
        external_id="pi_phase29_no_dup", payment_method="card",
        allocations=[AllocationInput(invoice_id=invoice.id, amount=Decimal("100.00"))],
    )
    assert deduped1 is False

    payment2, deduped2 = await service.record_payment(
        tenant_id, customer_id=customer.id, amount=Decimal("100.00"), provider="stripe",
        external_id="pi_phase29_no_dup", payment_method="card",
        allocations=[AllocationInput(invoice_id=invoice.id, amount=Decimal("100.00"))],
    )
    assert deduped2 is True
    assert payment2.id == payment1.id

    async with async_session_maker() as session:
        events = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "payment.received"))
        ).scalars().all()
        assert len(events) == 1, "a genuinely-already-published event must never be duplicated by a later dedup call"
