"""Phase 3 (Recommendation Engine): consumes the canonical Business
Blueprint (Phase 2) plus Phase 1's VerticalExtension / IntegrationProviderCatalog
/ Tool Catalog (ToolRegistry) to produce structured, explainable, governed
recommendations for capabilities/integrations/tools a business may need.

Recommendations != actions: a Recommendation row never itself connects a
provider, creates credentials, executes a tool, sends a message, or
creates an agent — it only records a possible need and, where matched, a
possible provider/tool that could satisfy it. See
app/services/recommendation_service.py's module docstring for the full
generation pipeline and app/api/v1/recommendations.py for the explicit
accept/reject action endpoints (never a generic PATCH).

Domain-agnostic by construction, mirroring app/models/business_blueprint.py:
no code here or in the service layer ever branches on a vertical-name
string — a vertical contributes candidate capabilities only via its own
`VerticalExtension.capabilities` registry row, looked up by
`OrganizationVerticalExtension` enablement, never a hardcoded switch. See
tests/test_vertical_extension_no_hardcoding_guard.py, which this file's
code must never trip.

Naming/reconciliation decisions (both KLAROS_FINAL_DOMAIN_MODEL.md and
KLAROS_INTEGRATION_MARKETPLACE_SPEC.md describe an overlapping but not
identical Recommendation shape, and no code precedent existed before this
phase — see PHASE_3_IMPLEMENTATION_LOG.md "Recommendation model" for the
full writeup):

  - Lifecycle is PROPOSED/ACCEPTED/REJECTED/SUPERSEDED (not the Domain
    Model doc's PROPOSED/ACCEPTED/DISMISSED) — REJECTED/SUPERSEDED mirrors
    `BlueprintClaim.ClaimStatus` exactly, this codebase's own precedent
    for a claim-like row with an explicit human decision plus
    version-driven invalidation, and an explicit accept/reject action-pair
    matches the Blueprint's confirm/reject convention
    (KLAROS_FINAL_API_ARCHITECTURE.md: "explicit action endpoints, never a
    generic PATCH").
  - The class is named `Recommendation` / table `recommendations` —
    deliberately NOT reusing `app/models/morning_brief.py`'s existing
    `MorningBriefRecommendation` / `RecommendationStatus` /
    `Permission.EXECUTE_RECOMMENDATION`, which is a different,
    already-shipped concept (morning-brief action suggestions with
    execute semantics) this Recommendation Engine must never touch,
    extend, or be confused with.
  - The 8 fields KLAROS_FINAL_TESTING_ARCHITECTURE.md requires every
    recommendation to carry — WHY / WHAT / DEPENDENCIES / COST /
    REQUIRED-OR-OPTIONAL / ALTERNATIVES / CONFIDENCE / SOURCE — map
    one-to-one onto this model's `why`, `what`, `dependencies`,
    `cost_estimate`, `required`, `alternatives`, `confidence`, `source`
    columns.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class RecommendationType(StrEnum):
    """Restricted to what this phase is authorized to identify — never
    Agent/Website/Marketplace (explicit scope boundary of Phase 3)."""

    CAPABILITY = "CAPABILITY"
    INTEGRATION = "INTEGRATION"
    TOOL = "TOOL"


class RecommendationSource(StrEnum):
    """What MECHANISM produced the structural (what-to-recommend) decision
    — deliberately excludes a bare "AI" value. AI is never the source of
    the structural decision in this implementation
    (KLAROS_MASTER_IMPLEMENTATION_ROADMAP.md Phase 4: "never for the
    structural decision of *what* to recommend, keeping recommendations
    auditable and reproducible") — see recommendation_service.py's module
    docstring for why this implementation found deterministic matching
    sufficient for every step and did not need AI at all."""

    BASELINE_RULE = "BASELINE_RULE"
    VERTICAL_EXTENSION_RULE = "VERTICAL_EXTENSION_RULE"


class RecommendationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class RecommendationRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RecommendationRun(TenantScopedMixin, Base):
    """One execution of the Recommendation Engine against one specific
    Blueprint version — the traceability envelope this phase's Blueprint-
    version-binding requirement calls for (KLAROS_FINAL_DOMAIN_MODEL.md /
    this task's explicit "RecommendationRun-shaped concept" instruction).
    A new Blueprint version requires a new run; a prior run's
    recommendations stay bound to the blueprint version they were
    generated against — see
    recommendation_service.py::RecommendationService.generate_recommendations."""

    __tablename__ = "recommendation_runs"

    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=False, index=True
    )
    blueprint_version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RecommendationRunStatus.COMPLETED)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Snapshot of which VerticalExtension.key values were considered by
    # this run (the org's enabled verticals at generation time) — pure
    # traceability, never re-branched-on.
    verticals_considered: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recommendation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_recommendation_runs_tenant_blueprint", "tenant_id", "blueprint_id"),)


class Recommendation(TenantScopedMixin, Base):
    __tablename__ = "recommendations"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recommendation_runs.id"), nullable=False, index=True
    )
    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=False, index=True
    )
    # Denormalized snapshot of the blueprint version this recommendation
    # was generated against — the concrete mechanism behind "old
    # recommendations stay historically understandable and are not
    # silently treated as current for a new version" (a new
    # BusinessBlueprint row = a new version, so this column, not a live
    # join, is what answers "which version produced this row").
    blueprint_version: Mapped[int] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    capability_key: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    # Populated only when type == INTEGRATION. Matched-by-value (not a DB
    # FK) against IntegrationProviderCatalog.provider_key — that table is
    # tenant-independent reference data looked up by key, matching how
    # every other consumer of the catalog (recommended_for_verticals tags,
    # etc.) references it, per app/models/integration_catalog.py's own
    # module docstring.
    provider_key: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # Verbatim snapshot of IntegrationProviderCatalog.implementation_status
    # at generation time — never re-derived or overridden later (not even
    # by AI narrative text), so a provider's catalog status change doesn't
    # silently rewrite history, and a STUB is never presented as
    # ready-to-connect (KLAROS_DO_NOT_BUILD_YET.md §9).
    provider_implementation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Populated only when type == TOOL. Matched-by-value against the live
    # Tool Catalog (app/services/tool_catalog_service.py ->
    # app.tools.registry.ToolRegistry) — validated to exist in the
    # registry at generation time, never trusted blindly, and never used
    # to call registry.execute()/get() with mutating intent.
    tool_name: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    what: Mapped[str] = mapped_column(Text, nullable=False)
    why: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured evidence list: each entry identifies a BlueprintClaim id,
    # a section_key, or a VerticalExtension key that this recommendation is
    # BASED-ON — never an opaque prose blob (KLAROS_FINAL_TESTING_
    # ARCHITECTURE.md's WHAT/WHY/BASED-ON-WHAT rendering requirement).
    based_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dependencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cost_estimate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alternatives: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    source_vertical_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RecommendationStatus.PROPOSED, index=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_recommendations_tenant_blueprint_status", "tenant_id", "blueprint_id", "status"),
        Index("ix_recommendations_tenant_run", "tenant_id", "run_id"),
        # Duplicate-recommendation prevention within a single run: at most
        # one row per (run, type, capability, provider, tool) combination.
        # A plain multi-column UNIQUE index would NOT catch two CAPABILITY
        # rows (provider_key/tool_name both NULL) for the same capability,
        # because SQL treats NULL as distinct-from-itself in a unique
        # index — caught only against a real Postgres test
        # (tests/test_postgres_recommendation_rls_audit_mode.py), never on
        # SQLite. COALESCE to an empty-string sentinel (never a legal
        # provider_key/tool_name value) makes the two "not applicable"
        # columns comparable, closing that gap for every `type`, not only
        # INTEGRATION/TOOL. The service layer's own in-run `seen` set
        # (recommendation_service.py) is the primary defense; this index
        # is the DB-level backstop.
        Index(
            "uq_recommendations_run_type_capability_provider_tool",
            "run_id",
            "type",
            "capability_key",
            text("COALESCE(provider_key, '')"),
            text("COALESCE(tool_name, '')"),
            unique=True,
        ),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_recommendations_confidence_range"),
    )
