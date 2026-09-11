"""Phase 14: quote lifecycle — DRAFT -> SENT -> VIEWED -> ACCEPTED/DECLINED/
EXPIRED -> (ACCEPTED only) CONVERTED. Reuses `app/services/invoice_service.
py`'s deterministic pricing primitives (`LineItemInput`/`compute_line_total`/
`compute_totals`) rather than re-deriving them — a quote's line-item math
is identical to an invoice's, just for a different document.

The customer's own accept/decline decision (via the public, token-secured
view — see `app/core/security.py::create_quote_view_token`) IS the
approval boundary here; there is deliberately no internal staff
`ApprovalRequest` step before a quote is sent (unlike invoices, where
money is already owed) or before it converts to a job (the customer's
acceptance already *is* the go-ahead).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.crm import Customer
from app.models.event import EventType
from app.models.operations import Job
from app.models.quote import DepositType, Quote, QuoteLineItem, QuoteStatus
from app.services.invoice_service import LineItemInput, compute_line_total, compute_totals
from app.services.job_service import CreateJobInput, JobService

DEFAULT_VALID_DAYS = 30


class QuoteNotFoundError(Exception):
    pass


class CustomerNotFoundError(Exception):
    pass


class InvalidQuoteTransitionError(Exception):
    pass


class QuoteExpiredError(Exception):
    pass


class InvalidDepositError(Exception):
    pass


@dataclass
class QuoteDecisionResult:
    quote: Quote
    job: Job | None


def compute_deposit_amount(
    total: Decimal, *, deposit_type: str | None, deposit_value: Decimal | None
) -> Decimal | None:
    """Decimal-safe, never float. Returns None when no deposit is
    configured. Clamped to (0, total] — a deposit can never be zero-or-
    negative once configured, nor exceed the quote's own total."""
    if deposit_type is None or deposit_value is None:
        return None
    if deposit_type == DepositType.PERCENTAGE:
        if deposit_value <= 0 or deposit_value > 100:
            raise InvalidDepositError("Percentage deposit must be > 0 and <= 100")
        amount = (total * deposit_value / Decimal(100)).quantize(Decimal("0.01"))
    elif deposit_type == DepositType.FIXED:
        if deposit_value <= 0:
            raise InvalidDepositError("Fixed deposit must be > 0")
        amount = deposit_value.quantize(Decimal("0.01"))
    else:
        raise InvalidDepositError(f"Unknown deposit_type: {deposit_type}")
    if amount > total:
        raise InvalidDepositError(f"Deposit amount (${amount}) cannot exceed the quote total (${total})")
    return amount


class QuoteService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._job_service = JobService(session_factory, bus)

    async def _next_quote_number(self, session, tenant_id: uuid.UUID) -> str:
        count = (
            await session.execute(select(func.count(Quote.id)).where(Quote.tenant_id == tenant_id))
        ).scalar_one()
        return f"QTE-{1000 + count + 1}"

    async def _replace_line_items(
        self, session, tenant_id: uuid.UUID, quote_id: uuid.UUID, items: list[LineItemInput]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        existing = (
            await session.execute(
                select(QuoteLineItem).where(
                    QuoteLineItem.tenant_id == tenant_id, QuoteLineItem.quote_id == quote_id
                )
            )
        ).scalars().all()
        for row in existing:
            await session.delete(row)
        await session.flush()

        for i, item in enumerate(items):
            session.add(
                QuoteLineItem(
                    tenant_id=tenant_id,
                    quote_id=quote_id,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    discount=item.discount,
                    tax_rate=item.tax_rate,
                    line_total=compute_line_total(item),
                    sort_order=i,
                )
            )
        return compute_totals(items)

    async def create_draft(
        self,
        tenant_id: uuid.UUID,
        *,
        customer_id: uuid.UUID,
        lead_id: uuid.UUID | None,
        items: list[LineItemInput],
        notes: str | None = None,
        terms: str | None = None,
        idempotency_key: str | None = None,
        deposit_type: str | None = None,
        deposit_value: Decimal | None = None,
    ) -> tuple[Quote, bool]:
        if deposit_type is not None and deposit_value is not None:
            # Validated eagerly against a total of Decimal("inf")-like
            # ceiling isn't meaningful here (the real total isn't computed
            # yet) — PERCENTAGE bounds are still checkable now; FIXED vs.
            # total is re-validated once the real total is known, below.
            if deposit_type == DepositType.PERCENTAGE and (deposit_value <= 0 or deposit_value > 100):
                raise InvalidDepositError("Percentage deposit must be > 0 and <= 100")
            if deposit_type == DepositType.FIXED and deposit_value <= 0:
                raise InvalidDepositError("Fixed deposit must be > 0")
        async with self._session_factory() as session:
            if idempotency_key:
                existing = (
                    await session.execute(
                        select(Quote).where(
                            Quote.tenant_id == tenant_id, Quote.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True

            customer = await session.get(Customer, customer_id)
            if customer is None or customer.tenant_id != tenant_id:
                raise CustomerNotFoundError("Customer not found")

            quote = Quote(
                tenant_id=tenant_id,
                quote_number=await self._next_quote_number(session, tenant_id),
                customer_id=customer_id,
                lead_id=lead_id,
                status=QuoteStatus.DRAFT,
                valid_until=date.today() + timedelta(days=DEFAULT_VALID_DAYS),
                notes=notes,
                terms=terms,
                idempotency_key=idempotency_key,
                deposit_type=deposit_type,
                deposit_value=deposit_value,
            )
            session.add(quote)
            await session.flush()

            subtotal, tax, discount, total = await self._replace_line_items(session, tenant_id, quote.id, items)
            quote.subtotal = subtotal
            quote.tax = tax
            quote.discount = discount
            quote.total = total
            if deposit_type is not None and deposit_value is not None:
                # Eagerly validated against the real total too (not just
                # deferred to accept time) so a staff member sees a
                # FIXED-deposit-exceeds-total mistake immediately.
                compute_deposit_amount(total, deposit_type=deposit_type, deposit_value=deposit_value)

            await session.commit()
            await session.refresh(quote)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.QUOTE_CREATED,
            source="quotes",
            entity_type="quote",
            entity_id=quote.id,
            payload={"quote_id": str(quote.id), "quote_number": quote.quote_number},
            idempotency_key=f"quote-created-{quote.id}",
        )
        return quote, False

    async def update_draft(
        self, tenant_id: uuid.UUID, quote_id: uuid.UUID, *, items: list[LineItemInput],
        notes: str | None = None, terms: str | None = None,
        deposit_type: str | None = None, deposit_value: Decimal | None = None,
        clear_deposit: bool = False,
    ) -> Quote:
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")
            if quote.status != QuoteStatus.DRAFT:
                raise InvalidQuoteTransitionError("Only a DRAFT quote can be edited")

            subtotal, tax, discount, total = await self._replace_line_items(session, tenant_id, quote.id, items)
            quote.subtotal = subtotal
            quote.tax = tax
            quote.discount = discount
            quote.total = total
            if notes is not None:
                quote.notes = notes
            if terms is not None:
                quote.terms = terms
            if clear_deposit:
                quote.deposit_type = None
                quote.deposit_value = None
            elif deposit_type is not None and deposit_value is not None:
                compute_deposit_amount(total, deposit_type=deposit_type, deposit_value=deposit_value)
                quote.deposit_type = deposit_type
                quote.deposit_value = deposit_value

            await session.commit()
            await session.refresh(quote)
        return quote

    async def send(self, tenant_id: uuid.UUID, quote_id: uuid.UUID, delivery_provider, view_url: str) -> Quote:
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")
            if quote.status != QuoteStatus.DRAFT:
                raise InvalidQuoteTransitionError("Only a DRAFT quote can be sent")

            customer = await session.get(Customer, quote.customer_id)

            quote.status = QuoteStatus.SENT
            quote.sent_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(quote)

        await delivery_provider.send_quote(
            tenant_id, quote_id=quote.id, customer_email=customer.email if customer else None,
            amount=quote.total, quote_number=quote.quote_number, view_url=view_url,
        )

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.QUOTE_SENT,
            source="quotes",
            entity_type="quote",
            entity_id=quote.id,
            payload={"quote_id": str(quote.id)},
        )
        return quote

    async def get_for_public_view(self, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> Quote:
        """Called only from the public, token-authenticated view endpoint
        — `tenant_id` here always comes from a verified `quote_view`
        token, never from unauthenticated request data. Marks VIEWED (and
        expires it first, if past `valid_until`) as a real side effect of
        a real customer visit, not a fabricated status."""
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")

            if quote.status in (QuoteStatus.SENT, QuoteStatus.VIEWED) and self._is_expired(quote):
                quote.status = QuoteStatus.EXPIRED
                await session.commit()
                await session.refresh(quote)
                await self._bus.publish(
                    tenant_id=tenant_id, event_type=EventType.QUOTE_EXPIRED, source="quotes",
                    entity_type="quote", entity_id=quote.id, payload={"quote_id": str(quote.id)},
                )
                return quote

            if quote.status == QuoteStatus.SENT:
                quote.status = QuoteStatus.VIEWED
                quote.viewed_at = datetime.now(timezone.utc)
                await session.commit()
                await session.refresh(quote)
                await self._bus.publish(
                    tenant_id=tenant_id, event_type=EventType.QUOTE_VIEWED, source="quotes",
                    entity_type="quote", entity_id=quote.id, payload={"quote_id": str(quote.id)},
                )
            return quote

    @staticmethod
    def _is_expired(quote: Quote) -> bool:
        return quote.valid_until is not None and date.today() > quote.valid_until

    async def decide(
        self, tenant_id: uuid.UUID, quote_id: uuid.UUID, *, accepted: bool, decline_reason: str | None = None,
    ) -> QuoteDecisionResult:
        """The customer's decision, made through the public view — not an
        internal `ApprovalRequest`/staff action. Accepting a quote with NO
        deposit configured immediately converts it into a real Job
        (unchanged Phase 14 behavior). Accepting a quote WITH a deposit
        configured instead freezes `deposit_amount` and moves to
        DEPOSIT_PENDING — the Job is only created once the deposit is
        actually paid, via `mark_deposit_paid` (reusing `JobService.
        create_job`, idempotent via `quote-{quote_id}` so a doubled
        request/replayed link can never create two jobs)."""
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")

            if quote.status == QuoteStatus.EXPIRED or self._is_expired(quote):
                raise QuoteExpiredError("This quote has expired and can no longer be decided")
            if quote.status in (
                QuoteStatus.ACCEPTED, QuoteStatus.DECLINED, QuoteStatus.DEPOSIT_PENDING,
                QuoteStatus.DEPOSIT_PAID, QuoteStatus.CONVERTED,
            ):
                raise InvalidQuoteTransitionError(f"Quote is already {quote.status}")
            if quote.status not in (QuoteStatus.SENT, QuoteStatus.VIEWED):
                raise InvalidQuoteTransitionError("Quote must be sent before it can be decided")

            deposit_required = accepted and quote.deposit_type is not None and quote.deposit_value is not None
            if deposit_required:
                quote.deposit_amount = compute_deposit_amount(
                    quote.total, deposit_type=quote.deposit_type, deposit_value=quote.deposit_value
                )
                quote.status = QuoteStatus.DEPOSIT_PENDING
            else:
                quote.status = QuoteStatus.ACCEPTED if accepted else QuoteStatus.DECLINED
            quote.decided_at = datetime.now(timezone.utc)
            if not accepted:
                quote.decline_reason = decline_reason

            customer_id = quote.customer_id
            lead_id = quote.lead_id
            quote_title = f"Quote {quote.quote_number}"
            quote_total = quote.total

            await session.commit()
            await session.refresh(quote)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.QUOTE_ACCEPTED if accepted else EventType.QUOTE_DECLINED,
            source="quotes",
            entity_type="quote",
            entity_id=quote.id,
            payload={"quote_id": str(quote.id)},
        )

        if not accepted:
            return QuoteDecisionResult(quote=quote, job=None)

        if deposit_required:
            await self._bus.publish(
                tenant_id=tenant_id,
                event_type=EventType.QUOTE_DEPOSIT_REQUIRED,
                source="quotes",
                entity_type="quote",
                entity_id=quote.id,
                payload={"quote_id": str(quote.id), "deposit_amount": str(quote.deposit_amount)},
            )
            return QuoteDecisionResult(quote=quote, job=None)

        job, refreshed_quote = await self._convert_to_job(
            tenant_id, quote_id=quote.id, customer_id=customer_id, lead_id=lead_id,
            quote_title=quote_title, quote_total=quote_total,
        )
        return QuoteDecisionResult(quote=refreshed_quote, job=job)

    async def _convert_to_job(
        self, tenant_id: uuid.UUID, *, quote_id: uuid.UUID, customer_id: uuid.UUID,
        lead_id: uuid.UUID | None, quote_title: str, quote_total: Decimal,
    ) -> tuple[Job, Quote]:
        job, _dedup = await self._job_service.create_job(
            tenant_id,
            CreateJobInput(
                title=quote_title,
                customer_id=customer_id,
                lead_id=lead_id,
                quote_id=quote_id,
                estimated_revenue=float(quote_total),
                idempotency_key=f"job-from-quote-{quote_id}",
            ),
        )

        async with self._session_factory() as session:
            refreshed_quote = await session.get(Quote, quote_id)
            refreshed_quote.status = QuoteStatus.CONVERTED
            refreshed_quote.job_id = job.id
            await session.commit()
            await session.refresh(refreshed_quote)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.QUOTE_CONVERTED_TO_JOB,
            source="quotes",
            entity_type="quote",
            entity_id=refreshed_quote.id,
            payload={"quote_id": str(refreshed_quote.id), "job_id": str(job.id)},
        )
        return job, refreshed_quote

    async def mark_deposit_paid(
        self, tenant_id: uuid.UUID, quote_id: uuid.UUID, *, payment_id: uuid.UUID,
    ) -> QuoteDecisionResult:
        """Called only from the Stripe webhook once a deposit PaymentIntent
        genuinely succeeds (see app/api/v1/webhooks.py). Idempotent: a
        replayed/duplicate webhook for a quote already past DEPOSIT_PENDING
        is a safe no-op that returns the current state, not an error — the
        underlying Payment row's own (tenant_id, provider, external_id)
        uniqueness is the first line of defense (see PaymentService.
        record_payment); this is the second, at the quote-state level.

        Phase 23 fix: CONVERTED always implies a real `job_id` (the two are
        set together, atomically, in `_convert_to_job`) — but DEPOSIT_PAID
        does NOT, if a previous call's own `_convert_to_job` step failed
        partway (e.g. a transient error inside `JobService.create_job`)
        after this quote's own status commit had already landed. The old
        idempotency check here treated ANY DEPOSIT_PAID/CONVERTED quote as
        "already fully handled, nothing left to do" and returned immediately
        — so a genuinely retried call (via the webhook's own redelivery fix,
        or a Stripe/operator resend) would silently report success with
        `job=None`, the customer's deposit permanently orphaned with no Job.
        Retrying `_convert_to_job` specifically when DEPOSIT_PAID but
        `job_id` is still unset closes that gap, relying on
        `_convert_to_job`/`create_job`'s own existing idempotency
        (`job-from-quote-{quote_id}`) to make this safe even under
        concurrent retries."""
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")

            if quote.status == QuoteStatus.CONVERTED:
                job = await session.get(Job, quote.job_id) if quote.job_id else None
                return QuoteDecisionResult(quote=quote, job=job)
            if quote.status == QuoteStatus.DEPOSIT_PAID and quote.job_id is not None:
                job = await session.get(Job, quote.job_id)
                return QuoteDecisionResult(quote=quote, job=job)
            if quote.status not in (QuoteStatus.DEPOSIT_PENDING, QuoteStatus.DEPOSIT_PAID):
                raise InvalidQuoteTransitionError(
                    f"Cannot mark deposit paid: quote is {quote.status}, expected DEPOSIT_PENDING"
                )

            already_deposit_paid = quote.status == QuoteStatus.DEPOSIT_PAID
            customer_id = quote.customer_id
            lead_id = quote.lead_id
            quote_title = f"Quote {quote.quote_number}"
            quote_total = quote.total

            if not already_deposit_paid:
                quote.status = QuoteStatus.DEPOSIT_PAID
                await session.commit()
                await session.refresh(quote)

        # Phase 29 fix: publish unconditionally (not gated on
        # `not already_deposit_paid`), relying on the new deterministic
        # `idempotency_key` below for safety. Previously, if THIS publish
        # call failed right after the status commit above (a real,
        # reproduced scenario — e.g. a transient Redis/transport outage),
        # the exception propagated out of `mark_deposit_paid` before
        # `_convert_to_job` ever ran. A retry then landed with
        # `already_deposit_paid=True` (status already committed) and
        # — under the old `if not already_deposit_paid:` guard — never
        # attempted the publish again, even though `_convert_to_job` below
        # still correctly ran and created the Job. Confirmed by direct
        # reproduction: quote reaches CONVERTED with a real Job, but
        # `QUOTE_DEPOSIT_PAID` is never published, not on the failed
        # attempt, not on the successful retry — permanently lost, no
        # error surfaced after the first failure. `EventBus.publish`
        # already deduplicates on `idempotency_key`, so calling this
        # every time is a safe no-op once it truly has succeeded.
        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.QUOTE_DEPOSIT_PAID,
            source="quotes",
            entity_type="quote",
            entity_id=quote.id,
            payload={"quote_id": str(quote.id), "payment_id": str(payment_id)},
            idempotency_key=f"quote-deposit-paid-{quote.id}",
        )

        job, refreshed_quote = await self._convert_to_job(
            tenant_id, quote_id=quote.id, customer_id=customer_id, lead_id=lead_id,
            quote_title=quote_title, quote_total=quote_total,
        )
        return QuoteDecisionResult(quote=refreshed_quote, job=job)

    async def detect_expired(self, tenant_id: uuid.UUID, *, as_of: date | None = None) -> list[uuid.UUID]:
        """Deterministic sweep (same pattern as `ARService.detect_overdue`)
        — no Temporal workflow needed for this; a periodic/manual tool
        call is sufficient, same as invoices' overdue detection.

        Phase 29 fix: `.with_for_update(skip_locked=True)`. A real
        PostgreSQL concurrency test proved this sweep shares
        `ContractService.detect_pending()`'s pre-Phase-28-fix race: under
        genuinely overlapping transactions (forced with an
        `asyncio.Barrier` in the regression test — ordinary `asyncio.
        gather` against a fast local database doesn't reliably create
        enough overlap on its own, but real production contention, e.g.
        multiple scheduler replicas ticking together or a slower query
        elsewhere, can), N concurrent callers can all SELECT the same
        stale quote before any of them COMMITs its UPDATE, so each one
        independently reports the transition and publishes its own
        QUOTE_EXPIRED event. `skip_locked=True` makes concurrent sweeps
        each grab a disjoint set of not-yet-locked rows — a no-op on
        SQLite, so existing SQLite-backed tests are unaffected. See
        `tests/test_postgres_sweep_concurrency.py` for the real-Postgres
        regression proof."""
        today = as_of or date.today()
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Quote).where(
                        Quote.tenant_id == tenant_id,
                        Quote.status.in_([QuoteStatus.SENT, QuoteStatus.VIEWED]),
                        Quote.valid_until.is_not(None),
                        Quote.valid_until < today,
                    ).with_for_update(skip_locked=True)
                )
            ).scalars().all()
            ids = [r.id for r in rows]
            for row in rows:
                row.status = QuoteStatus.EXPIRED
            await session.commit()

        for quote_id in ids:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.QUOTE_EXPIRED, source="quotes",
                entity_type="quote", entity_id=quote_id, payload={"quote_id": str(quote_id)},
            )
        return ids
