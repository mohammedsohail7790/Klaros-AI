"""Phase 30: real PostgreSQL concurrency verification for the three
check-then-insert defects found during this phase's systemic audit of the
same class of bug Phase 29 found in `CollectionService.
schedule_next_action` — `LeadService.create_lead()`, `ContractService.
create_from_quote()`, and `RetentionService._create_opportunity()`. All
three had a real, pre-existing unique constraint that would correctly
reject a concurrent duplicate INSERT, but never caught the resulting
`IntegrityError` — meaning a genuinely concurrent duplicate call (a
webhook retry, a redelivered event) would crash instead of returning the
existing row. Each test proves: (a) no unhandled exception under real
concurrency, (b) exactly one row survives, (c) exactly one downstream
event.
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
from app.models.contract import Contract, ContractStatus
from app.models.quote import Quote, QuoteLineItem, QuoteStatus
from app.models.retention import OpportunityType, RetentionOpportunity
from app.services.contract_service import ContractService
from app.services.exception_service import ExceptionService
from app.services.lead_service import CreateLeadInput, LeadService
from app.services.retention_service import RetentionService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_CONCURRENCY = 10


# =====================================================================
# A. LeadService.create_lead()
# =====================================================================

@requires_real_postgres
async def test_concurrent_duplicate_lead_submissions_produce_exactly_one_lead() -> None:
    tenant_id = uuid.uuid4()
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = LeadService(async_session_maker, bus)
    key = f"lead-dup-{uuid.uuid4().hex}"

    async def submit():
        data = CreateLeadInput(name="Concurrent Lead", source="WEB", email="dup@example.com", idempotency_key=key)
        return await service.create_lead(tenant_id, data)

    results = await asyncio.gather(*[submit() for _ in range(_CONCURRENCY)], return_exceptions=True)
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"create_lead() raised under concurrency: {exceptions}"

    lead_ids = {lead.id for lead, _ in results}
    assert len(lead_ids) == 1, f"expected exactly one lead, got {len(lead_ids)}"

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == EventType.LEAD_CREATED))
        ).scalars().all()
    assert len(rows) == 1, f"expected exactly one LEAD_CREATED event, got {len(rows)}"


# =====================================================================
# B. ContractService.create_from_quote()
# =====================================================================

async def _seed_accepted_quote(tenant_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="PG Phase30 Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"Q-PG30-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=1000, tax=0, discount=0, total=Decimal("1000.00"),
            valid_until=date.today() + timedelta(days=10), sent_at=datetime.now(timezone.utc),
            decided_at=datetime.now(timezone.utc),
        )
        session.add(quote)
        await session.flush()
        session.add(QuoteLineItem(
            tenant_id=tenant_id, quote_id=quote.id, description="Work", quantity=1, unit_price=Decimal("1000.00"),
            discount=0, tax_rate=0, line_total=Decimal("1000.00"), sort_order=0,
        ))
        await session.commit()
        return quote.id


@requires_real_postgres
async def test_concurrent_duplicate_contract_creation_produces_exactly_one_contract() -> None:
    tenant_id = uuid.uuid4()
    quote_id = await _seed_accepted_quote(tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = ContractService(async_session_maker, bus)

    results = await asyncio.gather(
        *[service.create_from_quote(tenant_id, quote_id) for _ in range(_CONCURRENCY)], return_exceptions=True
    )
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"create_from_quote() raised under concurrency: {exceptions}"

    contract_ids = {contract.id for contract, _ in results}
    assert len(contract_ids) == 1, f"expected exactly one contract, got {len(contract_ids)}"

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(Contract).where(Contract.tenant_id == tenant_id, Contract.quote_id == quote_id))
        ).scalars().all()
    assert len(rows) == 1

    async with async_session_maker() as session:
        events = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == EventType.CONTRACT_CREATED))
        ).scalars().all()
    assert len(events) == 1, f"expected exactly one CONTRACT_CREATED event, got {len(events)}"


# =====================================================================
# C. RetentionService._create_opportunity() (via the real public method)
# =====================================================================

@requires_real_postgres
async def test_concurrent_duplicate_referral_opportunity_creation_produces_exactly_one_row() -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="PG Phase30 Referral Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        customer_id = customer.id

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    exception_service = ExceptionService(async_session_maker, bus)
    service = RetentionService(async_session_maker, bus, exception_service)

    results = await asyncio.gather(
        *[service.create_referral_eligibility_opportunity(tenant_id, customer_id, reason="Great feedback") for _ in range(_CONCURRENCY)],
        return_exceptions=True,
    )
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"create_referral_eligibility_opportunity() raised under concurrency: {exceptions}"

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(RetentionOpportunity).where(
                    RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.customer_id == customer_id,
                    RetentionOpportunity.type == OpportunityType.REFERRAL_ELIGIBLE,
                )
            )
        ).scalars().all()
    assert len(rows) == 1, f"expected exactly one retention opportunity, got {len(rows)}"

    async with async_session_maker() as session:
        events = (
            await session.execute(
                select(Event).where(Event.tenant_id == tenant_id, Event.event_type == EventType.RETENTION_OPPORTUNITY_CREATED)
            )
        ).scalars().all()
    assert len(events) == 1, f"expected exactly one RETENTION_OPPORTUNITY_CREATED event, got {len(events)}"
