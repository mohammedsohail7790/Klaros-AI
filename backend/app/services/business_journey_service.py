"""Phase 13 (Business Orchestration Foundation): the thin coordinator
service that gives a tenant a persistent, resumable position across the
already-existing Business Discovery (Phase 2), Business Blueprint
(Phase 2), and Recommendation Engine (Phase 3) subsystems.

Architectural invariant (see app/models/business_journey.py's module
docstring): this service owns ONLY sequencing state and references to the
real subsystem rows. Every actual domain decision is delegated to the
existing, authoritative service for that subsystem:

  - Discovery session creation/completion -> BusinessDiscoveryService
    (never re-implemented here; this service only reads
    DiscoverySession.status to decide whether a forward transition is
    legal).
  - Blueprint activation -> BusinessBlueprintService.activate() (the exact
    same minimum-bar check Phase 2 already enforces; this service never
    duplicates that check).
  - Recommendation generation -> RecommendationService.generate_recommendations()
    (the exact same deterministic pipeline Phase 3 already runs).

No new execution engine, no new workflow engine, no autonomous side
effects: a journey transition either (a) reads existing subsystem state,
or (b) calls one existing subsystem *write* method that a human action
explicitly triggered via app/api/v1/business_journey.py. Nothing in this
module ever connects an integration, creates an Agent, publishes a
Website, or executes a Recommendation/tool.

Idempotency / resume (mirrors this codebase's established precedent —
AutomationService's `except IntegrityError` dedup on a real unique
constraint, and BusinessBlueprintService.confirm_claim's "already in the
target state -> return as-is" convention):

  - `start_journey`: the partial unique index
    `uq_business_journeys_one_active_per_tenant` (0051 migration) is the
    real concurrency guard for "one active journey per tenant" — two
    simultaneous start calls race at the DB, the loser's INSERT raises
    IntegrityError, and this service catches it and returns the winner's
    row (read back from the DB), never raising to the caller.
  - Every forward-transition method (`complete_discovery`,
    `confirm_blueprint`, `generate_recommendations`, `complete`) is called
    with the journey's *current* persisted state re-read fresh every time
    (never trusted from a stale in-memory object), and short-circuits to
    a no-op "already there" return when the journey (or the subsystem
    resource it is about to attach) is already past that point — this is
    what makes retry-after-timeout and resume-after-crash both safe: the
    real subsystem row (DiscoverySession.status / BusinessBlueprint.status
    / an existing RecommendationRun for the current blueprint version) is
    the source of truth, not any client-supplied "did it work?" belief.

Tenant isolation: every method takes an explicit `tenant_id` (from
CurrentUser, never a request body) and filters every query by it, exactly
mirroring BusinessBlueprintService/RecommendationService's own convention.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.business_blueprint import BlueprintStatus, BusinessBlueprint
from app.models.business_discovery import DiscoverySessionStatus
from app.models.business_journey import ACTIVE_STATUSES, BusinessJourney, BusinessJourneyStatus
from app.models.recommendation import RecommendationRun
from app.services.business_blueprint_service import (
    BlueprintActivationError,
    BusinessBlueprintService,
)
from app.services.business_discovery_service import BusinessDiscoveryService
from app.services.recommendation_service import NoActiveBlueprintError, RecommendationService
from app.tools.registry import ToolRegistry


class BusinessJourneyNotFoundError(Exception):
    pass


class InvalidJourneyTransitionError(Exception):
    pass


@dataclass(frozen=True)
class JourneyActionResult:
    journey: BusinessJourney
    created: bool  # True only when this call itself performed the underlying subsystem action


class BusinessJourneyService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        discovery_service: BusinessDiscoveryService,
        blueprint_service: BusinessBlueprintService,
        recommendation_service: RecommendationService,
    ) -> None:
        self._session_factory = session_factory
        self._discovery = discovery_service
        self._blueprint = blueprint_service
        self._recommendations = recommendation_service

    # --- retrieval ---------------------------------------------------

    async def get_current(self, tenant_id: uuid.UUID) -> BusinessJourney | None:
        """The tenant's one active (non-terminal) journey, if any — also
        what a client calls to "resume": re-fetch this, then call whichever
        named action corresponds to `journey.status`."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (
                await session.execute(
                    select(BusinessJourney)
                    .where(
                        BusinessJourney.tenant_id == tenant_id,
                        BusinessJourney.status.in_([s.value for s in ACTIVE_STATUSES]),
                    )
                    .order_by(BusinessJourney.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def get_by_id(self, tenant_id: uuid.UUID, journey_id: uuid.UUID) -> BusinessJourney:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            journey = await session.get(BusinessJourney, journey_id)
            if journey is None or journey.tenant_id != tenant_id:
                raise BusinessJourneyNotFoundError(f"BusinessJourney {journey_id} not found")
            return journey

    async def list_journeys(self, tenant_id: uuid.UUID) -> list[BusinessJourney]:
        """Historical journeys too (a tenant may abandon/complete one and
        start another) — newest first."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(BusinessJourney)
                    .where(BusinessJourney.tenant_id == tenant_id)
                    .order_by(BusinessJourney.created_at.desc())
                )
            ).scalars().all()
            return list(rows)

    # --- start ---------------------------------------------------------

    async def start_journey(
        self, tenant_id: uuid.UUID, *, business_idea: str, created_by: uuid.UUID | None
    ) -> JourneyActionResult:
        """Idempotent duplicate-start handling: if the tenant already has
        an active journey, that journey is returned unchanged (never a
        second concurrent journey, never an error) — this is a deliberate
        product choice (KLAROS_POST_PHASE_12_END_TO_END_AUDIT.md: "one
        active journey per tenant"), not merely a race-safety fallback."""
        existing = await self.get_current(tenant_id)
        if existing is not None:
            return JourneyActionResult(journey=existing, created=False)

        # Serialize concurrent starts for the SAME tenant before ever
        # calling into Discovery. `uq_business_journeys_one_active_per_tenant`
        # (0051) is the real backstop for the journey row itself, but
        # BusinessBlueprintService.get_or_create_draft (Phase 2, never
        # modified by this phase) has its own pre-check-then-insert
        # sequence with no such guard, which start_session calls into via
        # get_or_create_draft — a genuine real-Postgres-only race caught
        # while building this phase (two concurrent first-ever journeys for
        # one tenant would otherwise both try to insert
        # `business_blueprints (tenant_id, version=1)` and one loses to
        # `uq_business_blueprints_tenant_version`, an unhandled
        # IntegrityError). Rather than patch Phase 2's service (out of this
        # phase's scope — "existing subsystem services remain authoritative
        # for their own domains"), this thin coordinator closes the race at
        # its own layer with a Postgres advisory lock keyed by tenant_id,
        # held only for the duration of "check again, then start" — a
        # no-op on SQLite (unit tests never run this concurrently).
        lock_session = self._session_factory()
        try:
            await set_tenant_context(lock_session, tenant_id)
            is_postgres = lock_session.get_bind().dialect.name == "postgresql"
            if is_postgres:
                await lock_session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(tenant_id)}
                )
                # Re-check now that we hold the lock — a concurrent caller
                # may have already finished and committed while we waited.
                existing = await self.get_current(tenant_id)
                if existing is not None:
                    return JourneyActionResult(journey=existing, created=False)

            # Delegates entirely to the existing Discovery service — this is
            # the ONLY place a journey ever creates a DiscoverySession, and
            # it does so via the real subsystem entry point, never by
            # inserting a DiscoverySession row itself.
            turn_result = await self._discovery.start_session(
                tenant_id, business_idea=business_idea, created_by=created_by
            )

            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                journey = BusinessJourney(
                    tenant_id=tenant_id,
                    status=BusinessJourneyStatus.DISCOVERY_ACTIVE,
                    discovery_session_id=turn_result.session.id,
                    created_by=created_by,
                )
                session.add(journey)
                try:
                    await session.flush()
                    session.add(
                        AuditLog(
                            tenant_id=tenant_id,
                            actor_type=ActorType.USER,
                            actor_id=created_by,
                            action="business_journey.start",
                            tool=None,
                            entity_type="business_journey",
                            entity_id=journey.id,
                            input_summary={"discovery_session_id": str(turn_result.session.id)},
                            result="success",
                        )
                    )
                    await session.commit()
                except IntegrityError:
                    # Lost the race against a concurrent start — the partial
                    # unique index caught it (belt-and-braces alongside the
                    # advisory lock above, e.g. against a caller that hit an
                    # older app-server process without this lock). The
                    # DiscoverySession we just created above is orphaned
                    # (harmless, tenant-scoped, no side effects beyond its
                    # own rows) but never attached to a second journey;
                    # return the winner's journey instead.
                    await session.rollback()
                    winner = await self.get_current(tenant_id)
                    if winner is not None:
                        return JourneyActionResult(journey=winner, created=False)
                    raise
                await session.refresh(journey)
                return JourneyActionResult(journey=journey, created=True)
        finally:
            # Commit (releasing the advisory lock, held only for this
            # critical section) rather than rollback, so the lock is
            # dropped as soon as possible; this session performed no writes
            # of its own beyond the lock call.
            await lock_session.commit()
            await lock_session.close()

    # --- checkpoint 1: discovery complete -> blueprint review -----------

    async def complete_discovery(
        self, tenant_id: uuid.UUID, journey_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> JourneyActionResult:
        journey = await self.get_by_id(tenant_id, journey_id)

        if journey.status in (BusinessJourneyStatus.COMPLETED, BusinessJourneyStatus.ABANDONED):
            raise InvalidJourneyTransitionError(f"Journey is {journey.status} — cannot advance")
        if journey.status != BusinessJourneyStatus.DISCOVERY_ACTIVE:
            # Already past this checkpoint — idempotent no-op (retry-safe).
            return JourneyActionResult(journey=journey, created=False)

        if journey.discovery_session_id is None:
            raise InvalidJourneyTransitionError("Journey has no linked DiscoverySession")
        discovery_session = await self._discovery.get_session(tenant_id, journey.discovery_session_id)
        if discovery_session.status != DiscoverySessionStatus.COMPLETED:
            raise InvalidJourneyTransitionError(
                f"DiscoverySession is {discovery_session.status}, not COMPLETED — "
                "discovery must finish (minimum-bar sections filled, or question cap reached) "
                "before the journey can advance to Blueprint review"
            )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(BusinessJourney, journey_id)
            row.status = BusinessJourneyStatus.BLUEPRINT_REVIEW
            row.blueprint_id = discovery_session.blueprint_id
            row.last_error = None
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="business_journey.complete_discovery",
                    tool=None,
                    entity_type="business_journey",
                    entity_id=row.id,
                    input_summary={"blueprint_id": str(discovery_session.blueprint_id)},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(row)
            return JourneyActionResult(journey=row, created=True)

    # --- checkpoint 2: human confirms blueprint -> blueprint active -----

    async def confirm_blueprint(
        self, tenant_id: uuid.UUID, journey_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> JourneyActionResult:
        journey = await self.get_by_id(tenant_id, journey_id)

        if journey.status in (BusinessJourneyStatus.COMPLETED, BusinessJourneyStatus.ABANDONED):
            raise InvalidJourneyTransitionError(f"Journey is {journey.status} — cannot advance")
        if journey.status not in (BusinessJourneyStatus.BLUEPRINT_REVIEW,):
            if journey.status in (
                BusinessJourneyStatus.BLUEPRINT_ACTIVE,
                BusinessJourneyStatus.RECOMMENDATIONS_READY,
            ):
                return JourneyActionResult(journey=journey, created=False)
            raise InvalidJourneyTransitionError(
                f"Journey is {journey.status} — Blueprint cannot be confirmed until Discovery is complete"
            )
        if journey.blueprint_id is None:
            raise InvalidJourneyTransitionError("Journey has no linked Blueprint")

        blueprint = await self._blueprint.get_by_id(tenant_id, journey.blueprint_id)
        created = False
        if blueprint.status == BlueprintStatus.DRAFT:
            # Delegates the ENTIRE activation decision (minimum-bar check,
            # section completeness) to BusinessBlueprintService.activate —
            # this is the human-confirmation boundary already defined by
            # Phase 2; the journey never re-implements or loosens it.
            try:
                blueprint = await self._blueprint.activate(
                    tenant_id, journey.blueprint_id, activated_by=actor_id
                )
                created = True
            except BlueprintActivationError as exc:
                raise InvalidJourneyTransitionError(str(exc)) from exc
        elif blueprint.status != BlueprintStatus.ACTIVE:
            # SUPERSEDED — should not normally happen mid-journey (nothing
            # in this phase edits a blueprint section after Discovery), but
            # never silently proceed past it.
            raise InvalidJourneyTransitionError(
                f"Blueprint is {blueprint.status} — cannot confirm a superseded blueprint"
            )
        # else: already ACTIVE (activated directly via the Blueprint API,
        # or this is a retry after a crash between activate() succeeding
        # and the journey row committing below) — resume by just advancing
        # the journey, never re-activating.

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(BusinessJourney, journey_id)
            row.status = BusinessJourneyStatus.BLUEPRINT_ACTIVE
            row.last_error = None
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="business_journey.confirm_blueprint",
                    tool=None,
                    entity_type="business_journey",
                    entity_id=row.id,
                    input_summary={"blueprint_id": str(blueprint.id), "blueprint_version": blueprint.version},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(row)
            return JourneyActionResult(journey=row, created=created)

    # --- checkpoint 3: recommendations -----------------------------------

    async def generate_recommendations(
        self,
        tenant_id: uuid.UUID,
        journey_id: uuid.UUID,
        *,
        tool_registry: ToolRegistry,
        actor_id: uuid.UUID | None,
    ) -> JourneyActionResult:
        journey = await self.get_by_id(tenant_id, journey_id)

        if journey.status in (BusinessJourneyStatus.COMPLETED, BusinessJourneyStatus.ABANDONED):
            raise InvalidJourneyTransitionError(f"Journey is {journey.status} — cannot advance")
        if journey.status == BusinessJourneyStatus.RECOMMENDATIONS_READY:
            return JourneyActionResult(journey=journey, created=False)
        if journey.status != BusinessJourneyStatus.BLUEPRINT_ACTIVE:
            raise InvalidJourneyTransitionError(
                f"Journey is {journey.status} — recommendations require a confirmed, ACTIVE Blueprint first"
            )
        if journey.blueprint_id is None:
            raise InvalidJourneyTransitionError("Journey has no linked Blueprint")

        blueprint = await self._blueprint.get_by_id(tenant_id, journey.blueprint_id)

        # Concurrency guard: RecommendationRun has no DB-level uniqueness on
        # (tenant, blueprint, version) — RecommendationService is Phase 3's
        # own authoritative engine and this phase deliberately never
        # modifies it (see this module's docstring). Instead, a Postgres
        # row lock (`SELECT ... FOR UPDATE`) on THIS journey's own row is
        # the serialization point: since there is at most one non-terminal
        # journey per tenant (the 0051 partial unique index), holding this
        # lock for the duration of "check for an existing run, else
        # generate one" makes concurrent generate-recommendations calls for
        # the same journey queue up rather than race — the second (and
        # later) caller re-checks for an existing run only after the first
        # has committed, and reuses it. A no-op on SQLite (unit tests),
        # which does not support row-level locking but also never runs
        # these calls concurrently.
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            locked = (
                await session.execute(
                    select(BusinessJourney).where(BusinessJourney.id == journey_id).with_for_update()
                )
            ).scalar_one()
            if locked.status == BusinessJourneyStatus.RECOMMENDATIONS_READY:
                # Someone else finished this while we waited for the lock.
                return JourneyActionResult(journey=locked, created=False)

            # Resume-safety: reuse an existing run already generated against
            # this exact blueprint version, rather than calling the (real,
            # side-effect-producing) engine again — covers both "duplicate
            # generate-recommendations retry" and "crash after
            # RecommendationService committed, before this journey row did".
            existing_run = await self._latest_run_for_blueprint(tenant_id, blueprint.id, blueprint.version)
            created = False
            if existing_run is not None:
                run = existing_run
            else:
                try:
                    run = await self._recommendations.generate_recommendations(
                        tenant_id, tool_registry=tool_registry, triggered_by=actor_id
                    )
                except NoActiveBlueprintError as exc:
                    raise InvalidJourneyTransitionError(str(exc)) from exc
                created = True

            row = locked
            row.status = BusinessJourneyStatus.RECOMMENDATIONS_READY
            row.recommendation_run_id = run.id
            row.last_error = None
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="business_journey.generate_recommendations",
                    tool=None,
                    entity_type="business_journey",
                    entity_id=row.id,
                    input_summary={"recommendation_run_id": str(run.id), "reused_existing_run": not created},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(row)
            return JourneyActionResult(journey=row, created=created)

    async def _latest_run_for_blueprint(
        self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID, blueprint_version: int
    ) -> RecommendationRun | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (
                await session.execute(
                    select(RecommendationRun)
                    .where(
                        RecommendationRun.tenant_id == tenant_id,
                        RecommendationRun.blueprint_id == blueprint_id,
                        RecommendationRun.blueprint_version == blueprint_version,
                    )
                    .order_by(RecommendationRun.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    # --- terminal transitions --------------------------------------------

    async def complete(
        self, tenant_id: uuid.UUID, journey_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> JourneyActionResult:
        """Explicit human "I'm done deciding what to do next" terminal
        marker — never automatic. Recommendations remain proposals; nothing
        about completing a journey executes any of them."""
        journey = await self.get_by_id(tenant_id, journey_id)
        if journey.status == BusinessJourneyStatus.COMPLETED:
            return JourneyActionResult(journey=journey, created=False)
        if journey.status == BusinessJourneyStatus.ABANDONED:
            raise InvalidJourneyTransitionError("Journey is ABANDONED — cannot complete")
        if journey.status != BusinessJourneyStatus.RECOMMENDATIONS_READY:
            raise InvalidJourneyTransitionError(
                f"Journey is {journey.status} — cannot complete until recommendations are ready"
            )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(BusinessJourney, journey_id)
            row.status = BusinessJourneyStatus.COMPLETED
            row.completed_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="business_journey.complete",
                    tool=None,
                    entity_type="business_journey",
                    entity_id=row.id,
                    input_summary={},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(row)
            return JourneyActionResult(journey=row, created=True)

    async def abandon(
        self, tenant_id: uuid.UUID, journey_id: uuid.UUID, *, actor_id: uuid.UUID | None, reason: str | None
    ) -> JourneyActionResult:
        journey = await self.get_by_id(tenant_id, journey_id)
        if journey.status == BusinessJourneyStatus.ABANDONED:
            return JourneyActionResult(journey=journey, created=False)
        if journey.status == BusinessJourneyStatus.COMPLETED:
            raise InvalidJourneyTransitionError("Journey is COMPLETED — cannot abandon")

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(BusinessJourney, journey_id)
            row.status = BusinessJourneyStatus.ABANDONED
            row.abandoned_at = datetime.now(timezone.utc)
            row.last_error = reason
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="business_journey.abandon",
                    tool=None,
                    entity_type="business_journey",
                    entity_id=row.id,
                    input_summary={"reason": reason},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(row)
            return JourneyActionResult(journey=row, created=True)
