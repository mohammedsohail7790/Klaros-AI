"""Phase 7: real PostgreSQL concurrency verification for appointment
double-booking. Skipped entirely unless DATABASE_URL points at a real
PostgreSQL instance (see tests/test_postgres_transactions.py's
`requires_real_postgres` marker, reused here) — a genuinely concurrent
race cannot be proven against SQLite, where this codebase gives each
writer its own serialized connection to the same file (see
app/db/session.py's comment).

This test is the one that FOUND the real defect fixed in
app/calendar/internal_test_adapter.py this phase: run against real
PostgreSQL 16.6 before the fix, 10 simultaneous bookers for the exact same
slot produced 10 successful, overlapping appointments — a genuine
phantom-read race the plain check-then-insert could never protect against.
The fix (`pg_advisory_xact_lock`, keyed per tenant+technician) makes this
test pass: exactly one booker succeeds, every other one is rejected.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.calendar.base import BookingRequest, DoubleBookingError
from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.crm import Appointment, Customer
from app.models.organization import Organization

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


async def _seed_tenant_and_customers(n: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with async_session_maker() as session:
        org = Organization(name="Concurrency Test Co", slug=f"concurrency-{uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()
        customers = [Customer(tenant_id=org.id, name=f"Caller {i}") for i in range(n)]
        session.add_all(customers)
        await session.commit()
        return org.id, [c.id for c in customers]


@requires_real_postgres
async def test_ten_simultaneous_bookers_for_the_same_slot_exactly_one_succeeds() -> None:
    tenant_id, customer_ids = await _seed_tenant_and_customers(10)
    calendar = InternalTestCalendarAdapter(async_session_maker)
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
    end = start + timedelta(hours=1)

    async def book(customer_id: uuid.UUID, label: str):
        try:
            appt = await calendar.create_event(BookingRequest(
                tenant_id=tenant_id, customer_id=customer_id, title=f"Booking by {label}",
                start_time=start, end_time=end,
            ))
            return "SUCCESS", appt.id
        except DoubleBookingError:
            return "REJECTED", None

    results = await asyncio.gather(*[book(cid, f"caller-{i}") for i, cid in enumerate(customer_ids)])
    successes = [r for r in results if r[0] == "SUCCESS"]
    rejections = [r for r in results if r[0] == "REJECTED"]

    assert len(successes) == 1, f"expected exactly one successful booking, got {len(successes)}"
    assert len(rejections) == 9

    # Prove it at the database level too — not just trusting the Python
    # return values.
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Appointment).where(
                    Appointment.tenant_id == tenant_id, Appointment.start_time == start,
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@requires_real_postgres
async def test_concurrent_bookings_for_different_slots_all_succeed() -> None:
    """The advisory lock must not over-serialize unrelated bookings — only
    genuine same-slot contention should be affected."""
    tenant_id, customer_ids = await _seed_tenant_and_customers(5)
    calendar = InternalTestCalendarAdapter(async_session_maker)
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=4)

    async def book(customer_id: uuid.UUID, offset_hours: int):
        start = base + timedelta(hours=offset_hours)
        appt = await calendar.create_event(BookingRequest(
            tenant_id=tenant_id, customer_id=customer_id, title="Booking", start_time=start, end_time=start + timedelta(hours=1),
        ))
        return appt.id

    results = await asyncio.gather(*[book(cid, i) for i, cid in enumerate(customer_ids)])
    assert len(set(results)) == 5  # five distinct real appointments, no false rejections


@requires_real_postgres
async def test_concurrent_bookings_for_different_tenants_never_contend() -> None:
    """The advisory lock key includes tenant_id — two different tenants
    booking the identical wall-clock slot must never block each other."""
    tenant_a, customers_a = await _seed_tenant_and_customers(1)
    tenant_b, customers_b = await _seed_tenant_and_customers(1)
    calendar = InternalTestCalendarAdapter(async_session_maker)
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=5)
    end = start + timedelta(hours=1)

    async def book(tenant_id, customer_id):
        appt = await calendar.create_event(BookingRequest(
            tenant_id=tenant_id, customer_id=customer_id, title="Booking", start_time=start, end_time=end,
        ))
        return appt.id

    results = await asyncio.gather(book(tenant_a, customers_a[0]), book(tenant_b, customers_b[0]))
    assert len(set(results)) == 2  # both succeeded — real per-tenant isolation of the lock
