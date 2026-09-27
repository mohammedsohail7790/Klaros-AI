"""Phase 13 (KLAROS_POST_PHASE_12_END_TO_END_AUDIT.md): the Business
Journey — a thin, tenant-owned coordinator record that gives a tenant a
persistent, resumable position across the existing Business Discovery
(Phase 2), Business Blueprint (Phase 2), and Recommendation Engine
(Phase 3) subsystems.

This is NOT a fourth business-logic engine. `BusinessJourney` owns only:
sequencing state (`status`), and references to the real subsystem rows it
has produced or is waiting on (`discovery_session_id`, `blueprint_id`,
`recommendation_run_id`). It never duplicates DiscoverySession/
BusinessBlueprint/RecommendationRun's own fields or lifecycle logic — see
app/services/business_journey_service.py's module docstring for exactly
how each transition delegates to the existing, authoritative service for
that subsystem.

Lifecycle (`BusinessJourneyStatus`), a deliberately small, linear state
machine mirroring this codebase's existing lifecycle-enum precedent
(DiscoverySessionStatus, BlueprintStatus, RecommendationStatus):

    DISCOVERY_ACTIVE -> BLUEPRINT_REVIEW -> BLUEPRINT_ACTIVE
        -> RECOMMENDATIONS_READY -> COMPLETED
    (any non-terminal state) -> ABANDONED

A journey starts directly in DISCOVERY_ACTIVE (the act of starting a
journey is itself starting a DiscoverySession — see the service's
`start_journey`), so there is no separate "NEW" row state to leave
dangling. Every forward transition is a human-triggered, explicitly named
action (never a generic PATCH) — see app/api/v1/business_journey.py.

One-active-journey-per-tenant (mirrors BusinessBlueprint's own
`uq_business_blueprints_one_active_per_tenant` partial unique index and
Website's one-PUBLISHED-version-per-website constraint, both in this
codebase already): enforced with a partial unique index on `tenant_id`
`WHERE status NOT IN ('COMPLETED', 'ABANDONED')`, never only an
application-level check — see 0051_business_journey.py.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class BusinessJourneyStatus(StrEnum):
    DISCOVERY_ACTIVE = "DISCOVERY_ACTIVE"
    BLUEPRINT_REVIEW = "BLUEPRINT_REVIEW"
    BLUEPRINT_ACTIVE = "BLUEPRINT_ACTIVE"
    RECOMMENDATIONS_READY = "RECOMMENDATIONS_READY"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


# Statuses that count as "the tenant's one active journey" for the partial
# unique index / `get_current` lookup below. Kept as a plain tuple (not
# derived by exclusion at import time) so the SQL text in the migration and
# this module can be visually diffed against each other by a reviewer.
TERMINAL_STATUSES = (BusinessJourneyStatus.COMPLETED, BusinessJourneyStatus.ABANDONED)
ACTIVE_STATUSES = tuple(s for s in BusinessJourneyStatus if s not in TERMINAL_STATUSES)


class BusinessJourney(TenantScopedMixin, Base):
    __tablename__ = "business_journeys"
    __table_args__ = (
        Index(
            "uq_business_journeys_one_active_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status NOT IN ('COMPLETED', 'ABANDONED')"),
            sqlite_where=text("status NOT IN ('COMPLETED', 'ABANDONED')"),
        ),
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=BusinessJourneyStatus.DISCOVERY_ACTIVE, index=True
    )
    # References to the real, authoritative subsystem rows this journey has
    # produced/attached — never a copy of their own fields.
    discovery_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("discovery_sessions.id"), nullable=True, index=True
    )
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=True, index=True
    )
    recommendation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recommendation_runs.id"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set only when a forward-transition attempt failed against a
    # subsystem precondition (e.g. discovery not yet COMPLETED) — purely
    # informational, never itself gates a later retry (the precondition is
    # re-checked fresh every call, never cached here).
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
