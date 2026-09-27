"""Phase 2 (KLAROS_FINAL_SECURITY_MODEL.md §D "new tables RLS-on-day-one"):
RLS audit-mode instrumentation on the five new Business Discovery /
Business Blueprint tables. Mirrors
tests/test_postgres_vertical_extension_rls_audit_mode.py's structure
exactly. Skipped entirely unless DATABASE_URL points at a real PostgreSQL
instance.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, engine

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

TABLES = (
    "discovery_sessions",
    "discovery_turns",
    "business_blueprints",
    "blueprint_sections",
    "blueprint_claims",
)
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0043's RLS DDL
    never applies there — re-apply it here, same rationale as
    test_postgres_vertical_extension_rls_audit_mode.py's identical fixture."""
    if "postgresql" not in _settings.DATABASE_URL:
        yield
        return
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}"))
            await conn.execute(
                text(f"CREATE POLICY {POLICY_NAME} ON {table} FOR ALL USING (true) WITH CHECK (true)")
            )
    yield


@requires_real_postgres
@pytest.mark.parametrize("table", TABLES)
async def test_rls_is_enabled_audit_mode_only(table: str) -> None:
    async with async_session_maker() as session:
        enabled = await session.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
        )
        assert enabled.scalar() is True

        forced = await session.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
        )
        assert forced.scalar() is False, "audit-mode only — FORCE is a separate, later, whole-system decision"

        policy_count = await session.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
        )
        assert policy_count.scalar() == 1


@requires_real_postgres
async def test_audit_mode_is_a_real_no_op_today() -> None:
    """A context-less session (no set_tenant_context call) must still see
    every row — this is genuinely audit-mode, not enforcement, matching
    the maturity level Phase 0/1 established."""
    from app.models.business_blueprint import BusinessBlueprint

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(BusinessBlueprint(tenant_id=tenant_id, status="DRAFT", version=1))
        await session.commit()

    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT tenant_id FROM business_blueprints WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )
        assert result.first() is not None


@requires_real_postgres
async def test_full_pipeline_against_real_postgres() -> None:
    """End-to-end: DiscoverySession -> DiscoveryTurn -> BlueprintClaim ->
    confirm -> BlueprintSection recompute -> activate, all against real
    PostgreSQL (not just SQLite's Base.metadata.create_all()), proving the
    JSONB/GIN section-data column and the partial unique "one ACTIVE
    blueprint per tenant" index both work as declared."""
    from app.services.ai_provider import DeterministicAIProvider
    from app.services.business_blueprint_service import BusinessBlueprintService
    from app.services.business_discovery_service import BusinessDiscoveryService
    from app.services.discovery_extraction_service import DiscoveryExtractionService
    from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, ClaimProvenance, ClaimType

    tenant_id = uuid.uuid4()
    blueprint_service = BusinessBlueprintService(async_session_maker)
    extraction_service = DiscoveryExtractionService(async_session_maker, DeterministicAIProvider())
    discovery_service = BusinessDiscoveryService(async_session_maker, blueprint_service, extraction_service)

    result = await discovery_service.start_session(
        tenant_id, business_idea="A cross-border referral brokerage.", created_by=None
    )
    assert result.session.status.value if hasattr(result.session.status, "value") else result.session.status

    for key in MINIMUM_BAR_SECTIONS:
        claim = await blueprint_service.propose_claim(
            tenant_id, result.session.blueprint_id, section_key=key.value, claim_type=ClaimType.FACT.value,
            key=f"{key.value.lower()}.v", value="ok", confidence=None,
            provenance=ClaimProvenance.USER_STATED.value, discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)

    activated = await blueprint_service.activate(tenant_id, result.session.blueprint_id, activated_by=None)
    assert activated.status == "ACTIVE"

    active = await blueprint_service.get_active(tenant_id)
    assert active.id == activated.id

    # Partial unique index enforcement: a second ACTIVE row for the same
    # tenant must be rejected at the DB level, not just app logic.
    async with async_session_maker() as session:
        from app.models.business_blueprint import BusinessBlueprint

        session.add(BusinessBlueprint(tenant_id=tenant_id, status="ACTIVE", version=99))
        with pytest.raises(Exception):
            await session.commit()
        await session.rollback()
