"""Phase 29: the sibling defect to `test_phase29_publish_after_commit_recovery.py`,
in `QuoteService.mark_deposit_paid` instead of `PaymentService.record_payment`.

`mark_deposit_paid` commits the quote's `DEPOSIT_PAID` status, then
publishes `QUOTE_DEPOSIT_PAID`, then calls `_convert_to_job`. If the
publish call fails (a real, reproduced transient-outage scenario), the
exception propagates before `_convert_to_job` ever runs. A retry lands
with `already_deposit_paid=True` (the status commit already landed) — the
existing Phase 23 fix correctly re-attempts `_convert_to_job` in that
case, so the Job still gets created — but the OLD `if not
already_deposit_paid:` guard around the publish call meant
`QUOTE_DEPOSIT_PAID` was never re-attempted, permanently and silently lost
even after the quote genuinely reaches CONVERTED with a real Job.

Fixed by publishing unconditionally with a new deterministic
`quote-deposit-paid-{quote.id}` idempotency key, relying on
`EventBus.publish`'s own dedup to make repeated calls safe."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.event import Event
from app.models.operations import Job
from app.models.quote import Quote
from app.services.payment_service import PaymentService
from app.services.quote_service import QuoteService

from tests.test_quote_deposit import _ctx, _create_and_send_quote

pytestmark = pytest.mark.asyncio


async def test_quote_deposit_paid_event_recovers_after_a_publish_failure_and_retry(
    client, tool_registry, event_bus: EventBus, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="40.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = quote.customer_id

    payment_service = PaymentService(async_session_maker, event_bus)
    payment, _dedup = await payment_service.record_payment(
        tenant_id, customer_id=customer_id, amount=Decimal("40.00"), provider="stripe",
        external_id="pi_phase29_deposit_repro", payment_method="card",
        allocations=[], quote_id=uuid.UUID(quote_id),
    )

    quote_service = QuoteService(async_session_maker, event_bus)
    original_publish = EventBus.publish

    async def _failing_publish(self, *args, **kwargs):
        if kwargs.get("event_type") == "quote.deposit_paid":
            raise ConnectionError("simulated transient Redis/transport outage")
        return await original_publish(self, *args, **kwargs)

    monkeypatch.setattr(EventBus, "publish", _failing_publish)

    with pytest.raises(ConnectionError):
        await quote_service.mark_deposit_paid(tenant_id, uuid.UUID(quote_id), payment_id=payment.id)

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.status == "DEPOSIT_PAID"
        assert quote.job_id is None

        events = (
            await session.execute(
                select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "quote.deposit_paid")
            )
        ).scalars().all()
        assert len(events) == 0, "the event genuinely was never published on the first (failed) attempt"

    # The transient issue clears; retry (e.g. a redelivered webhook, or the
    # existing FAILED-webhook-event reprocessing from Phase 23).
    monkeypatch.setattr(EventBus, "publish", original_publish)

    result = await quote_service.mark_deposit_paid(tenant_id, uuid.UUID(quote_id), payment_id=payment.id)
    assert result.quote.status == "CONVERTED"
    assert result.job is not None

    async with async_session_maker() as session:
        events_after = (
            await session.execute(
                select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "quote.deposit_paid")
            )
        ).scalars().all()
        assert len(events_after) == 1, "the retry must now genuinely publish the previously-lost event"

        jobs = (await session.execute(select(Job).where(Job.quote_id == uuid.UUID(quote_id)))).scalars().all()
        assert len(jobs) == 1, "still exactly one Job, never duplicated by the retry"


async def test_a_further_retry_after_genuine_success_does_not_duplicate_the_event(
    client, tool_registry, event_bus: EventBus
) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="25.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = quote.customer_id

    payment_service = PaymentService(async_session_maker, event_bus)
    payment, _dedup = await payment_service.record_payment(
        tenant_id, customer_id=customer_id, amount=Decimal("25.00"), provider="stripe",
        external_id="pi_phase29_no_dup", payment_method="card",
        allocations=[], quote_id=uuid.UUID(quote_id),
    )

    quote_service = QuoteService(async_session_maker, event_bus)
    result1 = await quote_service.mark_deposit_paid(tenant_id, uuid.UUID(quote_id), payment_id=payment.id)
    assert result1.quote.status == "CONVERTED"

    # A further call (e.g. yet another redelivered webhook) hits the
    # already-CONVERTED early-return path -- never re-publishes at all,
    # matching the existing, unchanged CONVERTED short-circuit.
    result2 = await quote_service.mark_deposit_paid(tenant_id, uuid.UUID(quote_id), payment_id=payment.id)
    assert result2.quote.status == "CONVERTED"
    assert result2.job.id == result1.job.id

    async with async_session_maker() as session:
        events = (
            await session.execute(
                select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "quote.deposit_paid")
            )
        ).scalars().all()
        assert len(events) == 1, "a genuinely-already-published event must never be duplicated"
