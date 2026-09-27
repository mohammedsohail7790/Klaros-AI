"""Phase 2 (KLAROS_BUSINESS_BLUEPRINT_SPEC.md, KLAROS_FINAL_BUSINESS_
BLUEPRINT.md, KLAROS_FINAL_DOMAIN_MODEL.md): the Business Blueprint — the
single canonical, queryable, versioned representation of "what this
tenant's business is". Discovery produces it; future phases (Recommendation
Engine, Website Builder, Agents — none built here) read it.

Domain-agnostic by construction: `BlueprintSection.data` and
`BlueprintClaim.value` are JSONB, and the only fixed vocabulary is the
20-key `BlueprintSectionKey` enum (KLAROS_BUSINESS_BLUEPRINT_SPEC.md §4),
which is a set of universal business facets (identity, customers, revenue,
...), not a vertical list. No code here or in the surrounding service layer
ever branches on a business_type/vertical string — a vertical's own
elaboration lives inside a section's JSONB payload and/or a
`VerticalExtension` registry reference on the blueprint (`vertical_extension_id`),
looked up by id/key, exactly like Phase 1's registry pattern. See
tests/test_vertical_extension_no_hardcoding_guard.py, which this file's
code must never trip.

Persistence model (KLAROS_BUSINESS_BLUEPRINT_SPEC.md §3), hybrid three-layer,
not one JSON blob and not 20+ fully-normalized tables:

    BusinessBlueprint (versioned envelope, one row per version)
      -> BlueprintSection (one row per fixed section_key, JSONB `data`)
        -> BlueprintClaim (atomic, confidence-scored, evidence-linked)

Versioning: whole-row, immutable-per-version. Editing a section on an
ACTIVE blueprint creates a brand-new BusinessBlueprint row
(version = prior + 1, status=ACTIVE) with its sections cloned from the
prior version except the edited one; the prior version's row flips to
SUPERSEDED and is retained, never mutated or deleted, so "what did we
believe the business looked like at time T" is answerable by reading a
specific version's rows (KLAROS_BUSINESS_BLUEPRINT_SPEC.md §6). While a
blueprint is still DRAFT (pre-activation), section edits/claim confirmations
happen in place on the same DRAFT row — no version is created until the
first activation, matching "Discovery fills in a DRAFT that hasn't been
proven live yet" (no historical "belief" to preserve before ACTIVE exists).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text, Uuid, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin

# GIN indexing (BlueprintSection.data) requires jsonb — plain json has no
# default GIN operator class on PostgreSQL. SQLite (the test suite's
# Base.metadata.create_all() engine) falls back to generic JSON.
_SECTION_DATA_TYPE = JSON().with_variant(JSONB(), "postgresql")


class BlueprintStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class BlueprintSectionKey(StrEnum):
    """Fixed enum (KLAROS_BUSINESS_BLUEPRINT_SPEC.md §4, literal) — never
    extended per-vertical; a vertical's extra shape lives inside a
    section's JSONB `data`, keyed under its own `vertical_extension` key,
    per KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md."""

    IDENTITY = "IDENTITY"
    INDUSTRY = "INDUSTRY"
    BUSINESS_MODEL = "BUSINESS_MODEL"
    CUSTOMERS = "CUSTOMERS"
    PRODUCTS_SERVICES = "PRODUCTS_SERVICES"
    SUPPLIERS_PROVIDERS = "SUPPLIERS_PROVIDERS"
    GEOGRAPHY = "GEOGRAPHY"
    CHANNELS = "CHANNELS"
    REVENUE = "REVENUE"
    CUSTOMER_JOURNEY = "CUSTOMER_JOURNEY"
    OPERATIONS = "OPERATIONS"
    FINANCE = "FINANCE"
    MARKETING = "MARKETING"
    COMMUNICATIONS = "COMMUNICATIONS"
    COMPLIANCE = "COMPLIANCE"
    REQUIRED_CAPABILITIES = "REQUIRED_CAPABILITIES"
    CONSTRAINTS = "CONSTRAINTS"
    ASSUMPTIONS = "ASSUMPTIONS"
    DECISIONS = "DECISIONS"
    GOALS = "GOALS"


# The minimum bar for DRAFT -> ACTIVE (KLAROS_BUSINESS_BLUEPRINT_SPEC.md §6:
# "minimum bar: IDENTITY, INDUSTRY, BUSINESS_MODEL, REQUIRED_CAPABILITIES
# all COMPLETE"). A fixed, generic (non-vertical-specific) rule.
MINIMUM_BAR_SECTIONS = (
    BlueprintSectionKey.IDENTITY,
    BlueprintSectionKey.INDUSTRY,
    BlueprintSectionKey.BUSINESS_MODEL,
    BlueprintSectionKey.REQUIRED_CAPABILITIES,
)


class BlueprintSectionStatus(StrEnum):
    EMPTY = "EMPTY"
    DRAFT = "DRAFT"
    COMPLETE = "COMPLETE"


class ClaimType(StrEnum):
    """KLAROS_BUSINESS_DISCOVERY_SPEC.md §2, literal 8 values."""

    FACT = "Fact"
    INFERENCE = "Inference"
    ASSUMPTION = "Assumption"
    REQUIREMENT = "Requirement"
    PREFERENCE = "Preference"
    CONSTRAINT = "Constraint"
    DECISION = "Decision"
    UNKNOWN = "Unknown"


class ClaimProvenance(StrEnum):
    """KLAROS_BUSINESS_DISCOVERY_SPEC.md §2 / KLAROS_BUSINESS_BLUEPRINT_
    SPEC.md §3, literal 3 values — the per-claim-type source mapping the
    Discovery Spec's table documents (Fact->USER_STATED,
    Inference/Requirement->AI_INFERRED, Assumption->SYSTEM_DEFAULT,
    Preference/Constraint/Decision->USER_STATED, Unknown->none).
    "Imported" provenance (mentioned in this task's own prompt) has no
    import mechanism in Phase 2 scope — deferred, see
    PHASE_2_IMPLEMENTATION_LOG.md Known Limitations."""

    USER_STATED = "USER_STATED"
    AI_INFERRED = "AI_INFERRED"
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"


class ClaimStatus(StrEnum):
    """KLAROS_FINAL_DOMAIN_MODEL.md's 4-value superset of the Blueprint
    Spec's 3-value enum (adds SUPERSEDED) — needed so a claim can be
    traced across blueprint versions without deleting history."""

    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class BusinessBlueprint(TenantScopedMixin, Base):
    __tablename__ = "business_blueprints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_business_blueprints_tenant_version"),
        # Partial unique index: at most one ACTIVE blueprint per tenant
        # (KLAROS_FINAL_DOMAIN_MODEL.md). DRAFT/SUPERSEDED rows are
        # unconstrained in count.
        Index(
            "uq_business_blueprints_one_active_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=BlueprintStatus.DRAFT, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reference to the Phase 1 VerticalExtension registry, never a
    # hardcoded vertical string (app/models/vertical_extension.py). Nullable
    # — a blueprint need not be tied to any registered vertical.
    vertical_extension_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("vertical_extensions.id"), nullable=True
    )
    # Full-row versioning (see module docstring): the version this row
    # supersedes, if any. Nullable for version 1.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class BlueprintSection(TenantScopedMixin, Base):
    __tablename__ = "blueprint_sections"
    __table_args__ = (
        UniqueConstraint("blueprint_id", "section_key", name="uq_blueprint_sections_blueprint_key"),
        Index("ix_blueprint_sections_data_gin", "data", postgresql_using="gin"),
    )

    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=False, index=True
    )
    section_key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BlueprintSectionStatus.EMPTY, index=True
    )
    data: Mapped[dict] = mapped_column(_SECTION_DATA_TYPE, nullable=False, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class BlueprintClaim(TenantScopedMixin, Base):
    __tablename__ = "blueprint_claims"
    __table_args__ = (
        Index("ix_blueprint_claims_tenant_blueprint_status", "tenant_id", "blueprint_id", "status"),
        Index("ix_blueprint_claims_tenant_claim_type", "tenant_id", "claim_type"),
    )

    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=False, index=True
    )
    section_key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    # Explicit FK back to the DiscoveryTurn that produced this claim
    # (implementation judgment call, both research passes recommended this
    # over string-parsing an `evidence` free-text pointer — see
    # PHASE_2_IMPLEMENTATION_LOG.md). Nullable for human-entered claims
    # (a PUT .../sections/{key} edit with no discovery turn behind it).
    discovery_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("discovery_turns.id"), nullable=True, index=True
    )
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClaimStatus.PROPOSED, index=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
