"""Phase 12B: real-PostgreSQL transaction/rollback/tenant-isolation
verification. Skipped entirely unless DATABASE_URL points at a real
postgresql driver — SQLite's relaxed constraint enforcement (see
CommunicationLog.status, widened in migration 0014) means these behaviors
must be proven against the real engine, not assumed from the SQLite suite."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.operations import Job

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _job_kwargs(tenant_id: uuid.UUID, job_number: str, idempotency_key: str | None) -> dict:
    return dict(
        tenant_id=tenant_id,
        customer_id=uuid.uuid4(),
        job_number=job_number,
        title="Verification job",
        idempotency_key=idempotency_key,
    )


@requires_real_postgres
async def test_duplicate_idempotency_key_in_same_transaction_rolls_back_entirely() -> None:
    """A real unique constraint (tenant_id, idempotency_key) — proves a
    failed INSERT rolls back the WHOLE transaction, not just the bad row: a
    first, otherwise-valid job inserted in the same transaction as a
    conflicting second job must not survive the rollback."""
    tenant_id = uuid.uuid4()
    key = f"dup-{uuid.uuid4()}"

    async with async_session_maker() as session:
        session.add(Job(**_job_kwargs(tenant_id, "J-1", key)))
        session.add(Job(**_job_kwargs(tenant_id, "J-2", key)))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with async_session_maker() as session:
        result = await session.execute(select(Job).where(Job.tenant_id == tenant_id))
        rows = result.scalars().all()
        assert rows == [], (
            "the first, non-conflicting job must NOT have survived — "
            "a real transaction rollback is all-or-nothing"
        )


@requires_real_postgres
async def test_same_idempotency_key_is_fine_across_different_tenants() -> None:
    """The unique constraint is scoped (tenant_id, idempotency_key) — two
    different tenants using the identical idempotency key must both
    succeed, proving the constraint is tenant-scoped, not global."""
    key = f"shared-{uuid.uuid4()}"
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    async with async_session_maker() as session:
        session.add(Job(**_job_kwargs(tenant_a, "A-1", key)))
        session.add(Job(**_job_kwargs(tenant_b, "B-1", key)))
        await session.commit()

    async with async_session_maker() as session:
        result = await session.execute(
            select(Job).where(Job.tenant_id.in_([tenant_a, tenant_b]))
        )
        assert len(result.scalars().all()) == 2


@requires_real_postgres
async def test_rollback_after_flush_leaves_no_partial_row() -> None:
    """Flushing assigns a real PK server-side without committing; an
    explicit rollback after that flush must still leave zero rows —
    proving flush is not a partial commit."""
    tenant_id = uuid.uuid4()

    async with async_session_maker() as session:
        job = Job(**_job_kwargs(tenant_id, "FLUSH-1", None))
        session.add(job)
        await session.flush()
        assert job.id is not None
        await session.rollback()

    async with async_session_maker() as session:
        result = await session.execute(select(Job).where(Job.tenant_id == tenant_id))
        assert result.scalars().all() == []


@requires_real_postgres
async def test_string_length_constraint_is_enforced_by_real_postgres() -> None:
    """SQLite silently truncates/accepts oversized VARCHAR values; real
    PostgreSQL raises. This is the exact class of bug that produced
    migration 0014 (communication_logs.status) — proven directly here so a
    future regression on any VARCHAR column is caught against real
    PostgreSQL even before it reaches production data."""
    from app.models.communication import CommunicationLog

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(
            CommunicationLog(
                tenant_id=tenant_id,
                channel="EMAIL",
                template="t",
                recipient="a@example.com",
                body="hi",
                status="X" * 41,  # exceeds String(40)
                provider="internal_test",
            )
        )
        with pytest.raises(DBAPIError, match="StringDataRightTruncationError"):
            await session.commit()
        await session.rollback()
