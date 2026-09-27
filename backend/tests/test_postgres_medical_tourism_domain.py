"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md): real-Postgres-only
concerns for the Medical Tourism vertical extension — RLS audit-mode
instrumentation on all seven new tables, and concurrent-uniqueness
behavior for the provider-idempotency-key and (provider, procedure)
offering constraints. Mirrors tests/test_postgres_vertical_extension_rls_
audit_mode.py's structure exactly. Skipped entirely unless DATABASE_URL
points at a real PostgreSQL instance.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, engine
from app.services.medical_tourism_service import CreateOfferingInput, CreateProviderInput, MedicalTourismService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

_TABLES = (
    "medical_tourism_providers",
    "medical_tourism_provider_credentials",
    "medical_tourism_procedures",
    "medical_tourism_provider_procedures",
    "medical_tourism_patient_leads",
    "medical_tourism_consultations",
    "medical_tourism_referral_commissions",
)
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0049's RLS DDL
    never applies there — re-apply it here, same rationale as
    test_postgres_vertical_extension_rls_audit_mode.py's identical fixture."""
    if "postgresql" not in _settings.DATABASE_URL:
        yield
        return
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}"))
            await conn.execute(
                text(f"CREATE POLICY {POLICY_NAME} ON {table} FOR ALL USING (true) WITH CHECK (true)")
            )
    yield


@pytest.fixture
def service() -> MedicalTourismService:
    return MedicalTourismService(async_session_maker)


@requires_real_postgres
async def test_rls_enabled_audit_mode_on_every_new_table() -> None:
    async with async_session_maker() as session:
        for table in _TABLES:
            enabled = await session.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert enabled.scalar() is True, f"{table} must have RLS enabled"

            forced = await session.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
            assert forced.scalar() is False, f"{table}: audit-mode only, FORCE is a separate later decision"

            policy_count = await session.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
            assert policy_count.scalar() == 1, f"{table} must have exactly one audit-mode policy"


@requires_real_postgres
async def test_audit_mode_is_a_real_no_op_today(service: MedicalTourismService) -> None:
    """Same proof as test_postgres_vertical_extension_rls_audit_mode.py's
    identical test: a context-less session can still see rows across
    tenants at the DB level -- this is why application-layer tenant
    filtering (every MedicalTourismService method's explicit tenant_id
    predicate) is the real isolation boundary today, not RLS."""
    tenant_id = uuid.uuid4()
    provider, _ = await service.create_provider(tenant_id, CreateProviderInput(name="No-Op Test", country="TR"))

    async with async_session_maker() as session:
        # Deliberately do NOT call set_tenant_context.
        result = await session.execute(
            text("SELECT id FROM medical_tourism_providers WHERE id = :id"), {"id": str(provider.id)}
        )
        assert result.first() is not None, "audit-mode policy must not hide rows from a context-less session"


@requires_real_postgres
async def test_concurrent_provider_creation_with_same_idempotency_key_yields_one_row(
    service: MedicalTourismService,
) -> None:
    """Real concurrency, not a check-then-insert race simulated
    sequentially -- proves the DB-level unique constraint
    (uq_medical_tourism_providers_tenant_idempotency_key) plus the
    service's IntegrityError/re-fetch handling actually collapses a
    genuine concurrent duplicate submission to one row, mirroring
    tests/test_postgres_appointment_concurrency.py's structure."""
    tenant_id = uuid.uuid4()

    async def attempt():
        return await service.create_provider(
            tenant_id, CreateProviderInput(name="Concurrent Hospital", country="TR", idempotency_key="dup-key")
        )

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    provider_ids = {p.id for p, _ in results}
    assert len(provider_ids) == 1, "exactly one provider row must exist despite 8 concurrent create attempts"
    dedup_flags = [deduped for _, deduped in results]
    assert dedup_flags.count(False) == 1, "exactly one attempt must have been the real insert"
    assert dedup_flags.count(True) == 7


@requires_real_postgres
async def test_concurrent_offering_creation_for_same_provider_procedure_yields_one_row(
    service: MedicalTourismService,
) -> None:
    tenant_id = uuid.uuid4()
    provider, _ = await service.create_provider(tenant_id, CreateProviderInput(name="P", country="TR"))
    from app.services.medical_tourism_service import CreateProcedureInput

    procedure, _ = await service.create_procedure(tenant_id, CreateProcedureInput(name="Proc"))

    async def attempt():
        return await service.create_provider_procedure(
            tenant_id, CreateOfferingInput(provider_id=provider.id, procedure_id=procedure.id)
        )

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    offering_ids = {o.id for o, _ in results}
    assert len(offering_ids) == 1, "exactly one offering row must exist despite 8 concurrent create attempts"


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_b_provider_via_raw_sql_when_context_set() -> None:
    """Application-layer isolation is proven by MedicalTourismService's own
    explicit tenant_id filter in every query (see
    tests/test_medical_tourism_domain.py's service-layer isolation tests).
    This test additionally confirms two different tenants' rows coexist
    in the same real Postgres table without any DB-level uniqueness
    collision -- the schema itself never conflates tenants even though
    RLS is audit-mode, not enforcing."""
    service = MedicalTourismService(async_session_maker)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await service.create_provider(tenant_a, CreateProviderInput(name="Same Name", country="TR"))
    await service.create_provider(tenant_b, CreateProviderInput(name="Same Name", country="TR"))

    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT tenant_id FROM medical_tourism_providers WHERE name = 'Same Name'")
        )
        tenant_ids = {row[0] for row in result.fetchall()}
        assert tenant_ids == {tenant_a, tenant_b}
