"""Phase 13 (Business Orchestration Foundation): real-Postgres concurrency
proof for the one-active-journey-per-tenant guarantee, plus RLS audit-mode
instrumentation on `business_journeys` — mirrors
tests/test_postgres_recommendation_rls_audit_mode.py's structure and
tests/test_postgres_automation_concurrency.py-style concurrent-request
proof exactly. Skipped entirely unless DATABASE_URL points at a real
PostgreSQL instance (conftest.py's SQLite-backed `_reset_database` rebuilds
the schema from `Base.metadata` directly, so the partial unique index still
applies there too via `sqlite_where`, but only Postgres actually proves
true concurrent-transaction safety).
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, engine
from app.services.business_journey_service import BusinessJourneyService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)

TABLE = "business_journeys"
POLICY_NAME = "tenant_isolation_audit_policy"


@pytest_asyncio.fixture(autouse=True)
async def _apply_audit_mode_rls_policy():
    """conftest.py's global `_reset_database` rebuilds the schema from
    `Base.metadata` directly, not via Alembic, so migration 0051's RLS DDL
    never applies there — re-apply it here, same rationale as
    test_postgres_recommendation_rls_audit_mode.py's identical fixture."""
    if "postgresql" not in _settings.DATABASE_URL:
        yield
        return
    async with engine.begin() as conn:
        await conn.execute(text(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {TABLE}"))
        await conn.execute(
            text(f"CREATE POLICY {POLICY_NAME} ON {TABLE} FOR ALL USING (true) WITH CHECK (true)")
        )
    yield


@requires_real_postgres
async def test_rls_is_enabled_audit_mode_only() -> None:
    async with async_session_maker() as session:
        enabled = await session.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": TABLE}
        )
        assert enabled.scalar() is True
        forced = await session.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": TABLE}
        )
        assert forced.scalar() is False
        policy_count = await session.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": TABLE}
        )
        assert policy_count.scalar() == 1


@requires_real_postgres
async def test_one_active_journey_partial_unique_index_exists() -> None:
    async with async_session_maker() as session:
        result = await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = :t "
                "AND indexname = 'uq_business_journeys_one_active_per_tenant'"
            ),
            {"t": TABLE},
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert "UNIQUE" in row
        assert "tenant_id" in row


@requires_real_postgres
async def test_five_concurrent_journey_starts_yield_exactly_one_active_journey() -> None:
    """The real concurrency proof: 5 simultaneous `start_journey` calls for
    the SAME tenant, each on its own DB connection/transaction, must result
    in exactly one row for that tenant with a non-terminal status — the
    partial unique index (not merely the service's own pre-check) is what
    makes this safe, since the pre-check-then-insert sequence in
    `start_journey` is itself racy without it.
    """
    from app.services.business_blueprint_service import BusinessBlueprintService
    from app.services.business_discovery_service import BusinessDiscoveryService
    from app.services.discovery_extraction_service import DiscoveryExtractionService
    from app.services.ai_provider import get_ai_provider
    from app.services.recommendation_service import RecommendationService

    tenant_id = uuid.uuid4()
    blueprint_service = BusinessBlueprintService(async_session_maker)
    extraction_service = DiscoveryExtractionService(async_session_maker, get_ai_provider())
    discovery_service = BusinessDiscoveryService(async_session_maker, blueprint_service, extraction_service)
    recommendation_service = RecommendationService(async_session_maker)
    journey_service = BusinessJourneyService(
        async_session_maker, discovery_service, blueprint_service, recommendation_service
    )

    async def _start(i: int):
        return await journey_service.start_journey(
            tenant_id, business_idea=f"Concurrent idea #{i}", created_by=None
        )

    results = await asyncio.gather(*[_start(i) for i in range(5)], return_exceptions=True)

    # No unhandled exceptions — start_journey's own IntegrityError handling
    # must absorb every race, never propagate to the caller.
    errors = [r for r in results if isinstance(r, Exception)]
    assert errors == [], f"start_journey raised under concurrency: {errors}"

    journey_ids = {r.journey.id for r in results}
    assert len(journey_ids) == 1, f"expected exactly one journey, got {journey_ids}"

    async with async_session_maker() as session:
        count = await session.execute(
            text(
                "SELECT count(*) FROM business_journeys WHERE tenant_id = :tid "
                "AND status NOT IN ('COMPLETED', 'ABANDONED')"
            ),
            {"tid": str(tenant_id)},
        )
        assert count.scalar() == 1


@requires_real_postgres
async def test_concurrent_generate_recommendations_reuses_single_run() -> None:
    """Duplicate-recommendation-generation idempotency under real
    concurrency: once a journey is BLUEPRINT_ACTIVE, N simultaneous
    `generate-recommendations` calls must settle on exactly one
    RecommendationRun for that blueprint version."""
    from app.models.business_blueprint import (
        MINIMUM_BAR_SECTIONS,
    )
    from app.services.business_blueprint_service import BusinessBlueprintService
    from app.services.business_discovery_service import BusinessDiscoveryService
    from app.services.discovery_extraction_service import DiscoveryExtractionService
    from app.services.ai_provider import get_ai_provider
    from app.services.recommendation_service import RecommendationService
    from app.tools.factory import build_tool_registry
    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    tenant_id = uuid.uuid4()
    blueprint_service = BusinessBlueprintService(async_session_maker)
    extraction_service = DiscoveryExtractionService(async_session_maker, get_ai_provider())
    discovery_service = BusinessDiscoveryService(async_session_maker, blueprint_service, extraction_service)
    recommendation_service = RecommendationService(async_session_maker)
    journey_service = BusinessJourneyService(
        async_session_maker, discovery_service, blueprint_service, recommendation_service
    )

    result = await journey_service.start_journey(tenant_id, business_idea="Concurrency test business", created_by=None)
    journey = result.journey

    blueprint = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)
    for key in MINIMUM_BAR_SECTIONS:
        await blueprint_service.update_section(
            tenant_id, blueprint.id, section_key=key.value, data={"filled": True}, updated_by=None
        )
    blueprint = await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    async with async_session_maker() as session:
        from app.models.business_journey import BusinessJourney, BusinessJourneyStatus

        row = await session.get(BusinessJourney, journey.id)
        row.status = BusinessJourneyStatus.BLUEPRINT_ACTIVE
        row.blueprint_id = blueprint.id
        await session.commit()

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    tool_registry = build_tool_registry(async_session_maker, bus)

    async def _generate():
        return await journey_service.generate_recommendations(
            tenant_id, journey.id, tool_registry=tool_registry, actor_id=None
        )

    results = await asyncio.gather(*[_generate() for _ in range(5)], return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert errors == [], f"generate_recommendations raised under concurrency: {errors}"

    run_ids = {r.journey.recommendation_run_id for r in results}
    assert len(run_ids) == 1, f"expected exactly one recommendation run, got {run_ids}"

    async with async_session_maker() as session:
        count = await session.execute(
            text(
                "SELECT count(*) FROM recommendation_runs WHERE tenant_id = :tid AND blueprint_id = :bid"
            ),
            {"tid": str(tenant_id), "bid": str(blueprint.id)},
        )
        assert count.scalar() == 1
