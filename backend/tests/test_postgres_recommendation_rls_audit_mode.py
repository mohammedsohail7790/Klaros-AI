"""Phase 3 (KLAROS_FINAL_SECURITY_MODEL.md §D "new tables RLS-on-day-one"):
RLS audit-mode instrumentation on the two new Recommendation Engine
tables. Mirrors tests/test_postgres_business_discovery_blueprint_rls_audit_mode.py's
structure exactly. Skipped entirely unless DATABASE_URL points at a real
PostgreSQL instance.
"""

import uuid

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

TABLES = ("recommendation_runs", "recommendations")
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0044's RLS DDL
    never applies there — re-apply it here, same rationale as
    test_postgres_business_discovery_blueprint_rls_audit_mode.py's
    identical fixture."""
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
    every row — this is genuinely audit-mode, not enforcement."""
    from app.models.business_blueprint import BusinessBlueprint
    from app.models.recommendation import RecommendationRun

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        blueprint = BusinessBlueprint(tenant_id=tenant_id, status="ACTIVE", version=1)
        session.add(blueprint)
        await session.flush()
        session.add(
            RecommendationRun(
                tenant_id=tenant_id, blueprint_id=blueprint.id, blueprint_version=1,
                status="COMPLETED", verticals_considered=[], recommendation_count=0,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT tenant_id FROM recommendation_runs WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )
        assert result.first() is not None


@requires_real_postgres
async def test_full_pipeline_against_real_postgres() -> None:
    """End-to-end against real PostgreSQL (not just SQLite's
    Base.metadata.create_all()): activate a blueprint, generate
    recommendations, verify the confidence CHECK constraint and the
    duplicate-prevention unique index both work as declared at the DB
    level."""
    from sqlalchemy.exc import IntegrityError

    from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, BlueprintSectionKey, ClaimProvenance, ClaimType
    from app.models.recommendation import Recommendation, RecommendationRun
    from app.services.business_blueprint_service import BusinessBlueprintService
    from app.services.recommendation_service import RecommendationService
    from app.tools.factory import build_tool_registry
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    tenant_id = uuid.uuid4()
    blueprint_service = BusinessBlueprintService(async_session_maker)
    recommendation_service = RecommendationService(async_session_maker)
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    tool_registry = build_tool_registry(async_session_maker, bus)

    blueprint = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)
    for key in MINIMUM_BAR_SECTIONS:
        if key == BlueprintSectionKey.REQUIRED_CAPABILITIES:
            continue
        claim = await blueprint_service.propose_claim(
            tenant_id, blueprint.id, section_key=key.value, claim_type=ClaimType.FACT.value,
            key=f"{key.value.lower()}.v", value="ok", confidence=None,
            provenance=ClaimProvenance.USER_STATED.value, discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)
    cap_claim = await blueprint_service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="required_capabilities.list", value=["accounting"],
        confidence=0.9, provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await blueprint_service.confirm_claim(tenant_id, cap_claim.id, confirmed_by=None)
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    run = await recommendation_service.generate_recommendations(
        tenant_id, tool_registry=tool_registry, triggered_by=None
    )
    assert run.recommendation_count >= 1

    recs = await recommendation_service.list_recommendations(tenant_id)
    assert recs

    # CHECK constraint enforcement: an out-of-range confidence must be
    # rejected at the DB level, not just app logic.
    async with async_session_maker() as session:
        session.add(
            Recommendation(
                tenant_id=tenant_id, run_id=run.id, blueprint_id=blueprint.id, blueprint_version=1,
                type="CAPABILITY", capability_key="bad_confidence_test", what="x", why="x",
                based_on=[], dependencies=[], required=False, alternatives=[], confidence=1.5,
                source="BASELINE_RULE", status="PROPOSED",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    # Duplicate-prevention unique index: a second identical
    # (run, type, capability, provider, tool) row must be rejected.
    existing = recs[0]
    async with async_session_maker() as session:
        session.add(
            Recommendation(
                tenant_id=tenant_id, run_id=existing.run_id, blueprint_id=blueprint.id, blueprint_version=1,
                type=existing.type, capability_key=existing.capability_key, provider_key=existing.provider_key,
                tool_name=existing.tool_name, what="dup", why="dup", based_on=[], dependencies=[],
                required=False, alternatives=[], confidence=0.5, source="BASELINE_RULE", status="PROPOSED",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
