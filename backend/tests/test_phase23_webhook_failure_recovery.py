"""Phase 23: a genuinely reproduced, previously-undiscovered defect in the
Stripe quote-deposit webhook path. Found while auditing STEP 7 ("Stripe
succeeds, local persistence fails") and STEP 12 (failure injection) of the
Phase 23 mission — not a race condition (Phase 22 already covers the
concurrent-duplicate-key race), but a *sequential* recovery gap: once
`JobService.create_job` fails for ANY reason during
`QuoteService.mark_deposit_paid` -> `_convert_to_job` (a transient DB error,
not the specific already-fixed duplicate-key IntegrityError), the customer's
deposit was recorded as paid but the quote was left permanently stuck at
DEPOSIT_PAID with no Job, and NO redelivery of the same event -- not
Stripe's own automatic retry, not an operator manually resending the event
from the Stripe Dashboard -- could ever recover it. Two independent bugs
combined to cause this:

1. `app/api/v1/webhooks.py`'s WebhookEvent dedup check treated ANY existing
   row for an event_id (including one stuck FAILED) as a permanent
   duplicate, so a redelivery was silently swallowed before ever reaching
   the handler again.
2. `QuoteService.mark_deposit_paid`'s own idempotency check treated
   DEPOSIT_PAID as "already fully handled" regardless of whether `job_id`
   was actually set, so even a genuinely-reprocessed event would silently
   report success without ever retrying `_convert_to_job`.

Fixed by: (1) only short-circuiting the webhook dedup check when the
existing row's status isn't FAILED, reusing that row for a genuine retry;
(2) making `mark_deposit_paid`'s idempotency check retry `_convert_to_job`
specifically when DEPOSIT_PAID but `job_id` is still unset, relying on
`_convert_to_job`/`create_job`'s own existing idempotency
(`job-from-quote-{quote_id}`) to make this safe. A side effect of fix (1)
also corrected `WebhookEvent.tenant_id` staying NULL on this failure path
(the uncaught exception previously skipped the tenant_id assignment
entirely) -- `_handle_quote_deposit_succeeded` now also catches a generic
Exception around `mark_deposit_paid`, matching the exact pattern already
used for the `record_payment` call right above it.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.finance import Payment
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.models.operations import Job
from app.models.quote import Quote
from app.services import job_service as job_service_module

from tests.test_quote_deposit import _WEBHOOK_SECRET, _ctx, _create_and_send_quote, _deposit_success_payload, _sign
from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_transient_job_creation_failure_is_recovered_by_a_redelivery(
    client, tool_registry, monkeypatch
) -> None:
    """Simulates: Stripe's PaymentIntent succeeds and Klaros records the
    Payment, but Job creation fails for a transient (non-duplicate-key)
    reason. Once the underlying issue clears, the SAME webhook event
    (representing either Stripe's own automatic retry or an operator
    resending it from the Dashboard) must actually recover the quote to
    CONVERTED with exactly one Job -- not silently report success with no
    Job, and not be swallowed as an unconditional duplicate."""
    monkeypatch.setattr(get_settings(), "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="40.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = str(quote.customer_id)
        deposit_amount = quote.deposit_amount

    payload = _deposit_success_payload(
        tenant_id=tenant_id, quote_id=quote_id, customer_id=customer_id,
        amount_cents=int(deposit_amount * 100),
    )
    headers = {"Stripe-Signature": _sign(payload)}

    original_create_job = job_service_module.JobService.create_job

    async def _transient_failure(self, *args, **kwargs):
        raise RuntimeError("simulated transient DB failure during job creation")

    monkeypatch.setattr(job_service_module.JobService, "create_job", _transient_failure)

    resp1 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    # A "mark_deposit_paid failed" outcome means the payment WAS recorded
    # but the resulting Job wasn't — a genuinely retryable failure (see
    # app/api/v1/webhooks.py's _RETRYABLE_ERROR_PREFIXES) — so this now
    # returns a non-2xx specifically so Stripe's own retry mechanism can
    # kick in too, not just a manual/operator resend.
    assert resp1.status_code == 502
    assert resp1.json()["status"] == "failed"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.status == "DEPOSIT_PAID"
        assert quote.job_id is None

        payments = (
            await session.execute(select(Payment).where(Payment.quote_id == uuid.UUID(quote_id)))
        ).scalars().all()
        assert len(payments) == 1, "the payment must be recorded even though Job creation failed"

        webhook_row = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.provider == "stripe"))
        ).scalar_one()
        assert webhook_row.status == WebhookProcessingStatus.FAILED
        assert webhook_row.tenant_id == tenant_id, "the audit row must record which tenant this failure affects"

    # The underlying transient issue clears -- restore the real create_job,
    # then redeliver the exact same event (Stripe's own retry, or an
    # operator's manual resend).
    monkeypatch.setattr(job_service_module.JobService, "create_job", original_create_job)

    resp2 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "processed"

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.status == "CONVERTED"
        assert quote.job_id is not None

        jobs = (
            await session.execute(select(Job).where(Job.quote_id == uuid.UUID(quote_id)))
        ).scalars().all()
        assert len(jobs) == 1

        payments = (
            await session.execute(select(Payment).where(Payment.quote_id == uuid.UUID(quote_id)))
        ).scalars().all()
        assert len(payments) == 1, "the retry must never create a second Payment"

        webhook_row = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.provider == "stripe"))
        ).scalar_one()
        assert webhook_row.status == WebhookProcessingStatus.PROCESSED


async def test_a_third_delivery_after_success_is_a_true_duplicate(client, tool_registry, monkeypatch) -> None:
    """Once an event has genuinely been PROCESSED, a further redelivery must
    go back to being an unconditional duplicate_ignored -- the FAILED-status
    reprocessing path must not leave the event permanently retryable."""
    monkeypatch.setattr(get_settings(), "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(
        tool_registry, tenant_id, ctx, deposit_type="FIXED", deposit_value="25.00"
    )
    await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        customer_id = str(quote.customer_id)
        deposit_amount = quote.deposit_amount

    payload = _deposit_success_payload(
        tenant_id=tenant_id, quote_id=quote_id, customer_id=customer_id,
        amount_cents=int(deposit_amount * 100),
    )
    headers = {"Stripe-Signature": _sign(payload)}

    resp1 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp1.json()["status"] == "processed"

    resp2 = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
    assert resp2.json()["status"] == "duplicate_ignored"

    async with async_session_maker() as session:
        jobs = (
            await session.execute(select(Job).where(Job.quote_id == uuid.UUID(quote_id)))
        ).scalars().all()
        assert len(jobs) == 1
