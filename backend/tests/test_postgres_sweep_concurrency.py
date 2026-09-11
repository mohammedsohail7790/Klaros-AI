"""Phase 29: real PostgreSQL concurrency verification for the two sweep
services Phase 28 flagged as sharing `ContractService.detect_pending()`'s
pre-fix unlocked-read pattern — `QuoteService.detect_expired()` and
`ARService.detect_overdue()`. Neither is assumed broken or safe; this file
exists to observe real behavior under genuine concurrent PostgreSQL
transactions (SQLite serializes writers to the same file and can never
prove a real race) and prove — or disprove — a defect with execution, not
inspection.
"""

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer, CustomerStatus
from app.models.event import Event, EventType
from app.models.finance import CollectionAction, Invoice, InvoiceStatus
from app.models.operations import ExceptionStatus, ExceptionType, OperationsException
from app.models.quote import Quote, QuoteStatus
from app.services.ar_service import ARService
from app.services.collection_service import CollectionService
from app.services.exception_service import ExceptionService
from app.services.quote_service import QuoteService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_CONCURRENCY = 10


async def _make_customer(tenant_id: uuid.UUID, *, name: str = "PG Sweep Customer") -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name=name, status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        return customer.id


async def _make_stale_quote(tenant_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"Q-PGSWEEP-{uuid.uuid4().hex[:8]}", customer_id=customer_id,
            status=QuoteStatus.SENT, currency="USD", subtotal=1000, tax=0, discount=0, total=Decimal("1000.00"),
            valid_until=date.today() - timedelta(days=1), sent_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)
        return quote.id


async def _make_many_stale_quotes(tenant_id: uuid.UUID, customer_id: uuid.UUID, *, count: int) -> list[uuid.UUID]:
    """A single stale quote does not reliably overlap under plain
    `asyncio.gather` against a fast local database — the whole
    SELECT+UPDATE+COMMIT round trip for one row completes in well under a
    millisecond, faster than asyncio's cooperative scheduling reliably
    interleaves 10 tasks. A larger candidate set makes each sweep's own
    SELECT+iterate+flush work take measurably longer, which is what
    actually opens the real overlap window this test needs — this is not
    an artificial trick, it is closer to a real production sweep (many
    stale quotes at once) than a single-row test would be."""
    ids = []
    async with async_session_maker() as session:
        for i in range(count):
            quote = Quote(
                tenant_id=tenant_id, quote_number=f"Q-PGSWEEP-{uuid.uuid4().hex[:8]}", customer_id=customer_id,
                status=QuoteStatus.SENT, currency="USD", subtotal=100, tax=0, discount=0, total=Decimal("100.00"),
                valid_until=date.today() - timedelta(days=1), sent_at=datetime.now(timezone.utc) - timedelta(days=10),
            )
            session.add(quote)
            ids.append(quote)
        await session.commit()
        return [q.id for q in ids]


async def _make_overdue_invoice(tenant_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number=f"INV-PGSWEEP-{uuid.uuid4().hex[:8]}", customer_id=customer_id,
            status=InvoiceStatus.SENT, issue_date=date.today() - timedelta(days=20),
            due_date=date.today() - timedelta(days=5), currency="USD",
            subtotal=Decimal("500.00"), tax=0, discount=0, total=Decimal("500.00"),
            amount_paid=0, amount_due=Decimal("500.00"),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)
        return invoice.id


async def _events_for(tenant_id: uuid.UUID, event_type: str, entity_id: uuid.UUID) -> list[Event]:
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Event).where(
                    Event.tenant_id == tenant_id, Event.event_type == event_type, Event.entity_id == entity_id,
                )
            )
        ).scalars().all()
    return list(rows)


def _quote_service(bus: EventBus) -> QuoteService:
    return QuoteService(async_session_maker, bus)


def _ar_service(bus: EventBus) -> ARService:
    exception_service = ExceptionService(async_session_maker, bus)
    from app.communications.internal_test_adapter import InternalTestCommunicationAdapter

    collection_service = CollectionService(async_session_maker, InternalTestCommunicationAdapter(async_session_maker))
    return ARService(async_session_maker, exception_service, collection_service)


# =====================================================================
# A. Quote expiration sweep
# =====================================================================

@requires_real_postgres
async def test_concurrent_quote_expiration_sweeps_produce_exactly_one_transition_and_event() -> None:
    """A single-quote version of this test does not reliably reproduce the
    race on a fast local database (verified directly during investigation
    — 20+ runs at 10-way concurrency, plus a 50-way run, never
    reproduced). A forced-`asyncio.Barrier` diagnostic against the exact
    same query/update shape DID prove the race exists (all workers see
    the row as still SENT before any of them commits). This test
    reproduces it reliably and naturally — no artificial barrier needed —
    against the REAL `QuoteService.detect_expired()` by using a large
    enough candidate batch that each sweep's own work takes long enough
    to genuinely overlap; see `_make_many_stale_quotes`'s own docstring."""
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tenant_id)
    quote_ids = await _make_many_stale_quotes(tenant_id, customer_id, count=200)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = _quote_service(bus)

    results = await asyncio.gather(
        *[service.detect_expired(tenant_id) for _ in range(_CONCURRENCY)], return_exceptions=True
    )
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"detect_expired() raised under concurrency: {exceptions}"

    total_flagged = sum(len(r) for r in results)  # sum of every concurrent call's own reported list

    async with async_session_maker() as session:
        expired_count = (
            await session.execute(
                select(Quote).where(Quote.tenant_id == tenant_id, Quote.status == QuoteStatus.EXPIRED)
            )
        ).scalars().all()
    assert len(expired_count) == len(quote_ids)  # state itself always ends up correct

    async with async_session_maker() as session:
        all_events = (
            await session.execute(
                select(Event).where(Event.tenant_id == tenant_id, Event.event_type == EventType.QUOTE_EXPIRED)
            )
        ).scalars().all()

    # The actual business invariant: exactly one QUOTE_EXPIRED event PER
    # QUOTE — not just "the final row state is correct" (which is true
    # even when duplicate events are published, since re-setting the same
    # status twice is a no-op at the row level; the bug is entirely in
    # duplicate reporting/events, invisible if you only check final state).
    assert len(all_events) == len(quote_ids), (
        f"expected exactly {len(quote_ids)} QUOTE_EXPIRED events (one per quote), got {len(all_events)} "
        f"({total_flagged} total flagged across {_CONCURRENCY} concurrent calls)"
    )
    assert total_flagged == len(quote_ids), f"expected exactly {len(quote_ids)} total flagged, got {total_flagged}"
    # No quote was reported by more than one caller.
    entity_ids_seen = [str(e.entity_id) for e in all_events]
    assert len(entity_ids_seen) == len(set(entity_ids_seen)), "a quote was reported expired by more than one caller"


@requires_real_postgres
async def test_concurrent_quote_expiration_tenant_isolation() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a = await _make_customer(tenant_a)
    customer_b = await _make_customer(tenant_b)
    quote_a = await _make_stale_quote(tenant_a, customer_a)
    # Tenant B has its own unrelated (non-stale) quote.
    async with async_session_maker() as session:
        quote_b = Quote(
            tenant_id=tenant_b, quote_number="Q-PGSWEEP-TB", customer_id=customer_b, status=QuoteStatus.SENT,
            currency="USD", subtotal=200, tax=0, discount=0, total=Decimal("200.00"),
            valid_until=date.today() + timedelta(days=30), sent_at=datetime.now(timezone.utc),
        )
        session.add(quote_b)
        await session.commit()
        await session.refresh(quote_b)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = _quote_service(bus)

    await asyncio.gather(*[service.detect_expired(tenant_b) for _ in range(_CONCURRENCY)])

    async with async_session_maker() as session:
        a_quote = await session.get(Quote, quote_a)
        b_quote = await session.get(Quote, quote_b.id)
    assert a_quote.status == QuoteStatus.SENT  # tenant B's sweep never touched tenant A's stale quote
    assert b_quote.status == QuoteStatus.SENT  # tenant B's own quote isn't stale, untouched too

    events_a = await _events_for(tenant_a, EventType.QUOTE_EXPIRED, quote_a)
    assert events_a == []


# =====================================================================
# B. Invoice overdue detection sweep
# =====================================================================

@requires_real_postgres
async def test_concurrent_invoice_overdue_sweeps_produce_exactly_one_outcome() -> None:
    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tenant_id)
    invoice_id = await _make_overdue_invoice(tenant_id, customer_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = _ar_service(bus)

    results = await asyncio.gather(
        *[service.detect_overdue(tenant_id) for _ in range(_CONCURRENCY)], return_exceptions=True
    )
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"detect_overdue() raised under concurrency: {exceptions}"

    async with async_session_maker() as session:
        invoice = await session.get(Invoice, invoice_id)
    assert invoice.status == InvoiceStatus.OVERDUE  # state itself always ends up correct

    async with async_session_maker() as session:
        exceptions_rows = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.entity_id == invoice_id,
                    OperationsException.type == ExceptionType.INVOICE_OVERDUE,
                )
            )
        ).scalars().all()
        collection_actions = (
            await session.execute(
                select(CollectionAction).where(
                    CollectionAction.tenant_id == tenant_id, CollectionAction.invoice_id == invoice_id,
                )
            )
        ).scalars().all()

    assert len(exceptions_rows) == 1, f"expected exactly one INVOICE_OVERDUE exception, got {len(exceptions_rows)}"
    assert len(collection_actions) == 1, f"expected exactly one CollectionAction, got {len(collection_actions)}"

    exception_created_events = await _events_for(tenant_id, EventType.EXCEPTION_CREATED, invoice_id)
    assert len(exception_created_events) == 1, (
        f"expected exactly one EXCEPTION_CREATED event, got {len(exception_created_events)}"
    )


@requires_real_postgres
async def test_concurrent_invoice_overdue_tenant_isolation() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    customer_a = await _make_customer(tenant_a)
    customer_b = await _make_customer(tenant_b)
    invoice_a = await _make_overdue_invoice(tenant_a, customer_a)
    # Tenant B has its own, unrelated, NOT-overdue invoice.
    async with async_session_maker() as session:
        invoice_b = Invoice(
            tenant_id=tenant_b, invoice_number="INV-PGSWEEP-TB", customer_id=customer_b, status=InvoiceStatus.SENT,
            issue_date=date.today(), due_date=date.today() + timedelta(days=30), currency="USD",
            subtotal=Decimal("300.00"), tax=0, discount=0, total=Decimal("300.00"), amount_paid=0,
            amount_due=Decimal("300.00"),
        )
        session.add(invoice_b)
        await session.commit()
        await session.refresh(invoice_b)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = _ar_service(bus)

    await asyncio.gather(*[service.detect_overdue(tenant_b) for _ in range(_CONCURRENCY)])

    async with async_session_maker() as session:
        a_invoice = await session.get(Invoice, invoice_a)
        b_invoice = await session.get(Invoice, invoice_b.id)
    assert a_invoice.status == InvoiceStatus.SENT  # tenant B's sweep never touched tenant A's overdue invoice
    assert b_invoice.status == InvoiceStatus.SENT  # tenant B's own invoice isn't overdue yet, untouched

    events_a = await _events_for(tenant_a, EventType.EXCEPTION_CREATED, invoice_a)
    assert events_a == []
