"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md): the Website Builder's
relational domain model — the minimum normalized schema the design doc
settles on (§2/§3/§4): a `Website` envelope, full-row-immutable-per-version
`WebsiteVersion`s, and `WebsitePage`/`WebsiteSection` children. Theme,
navigation, and SEO defaults are structured JSONB *on* `WebsiteVersion`
(§5/§6/§7 of the design doc explain why those are not separate tables).

Domain-agnostic by construction, mirroring `app/models/business_blueprint.py`
and `app/models/recommendation.py`: nothing here ever branches on a vertical
name string. Vertical-specific content is bound only through
`WebsiteSection.data_source` (`{"provider_key": ..., "params": ...}`), a
generic pointer resolved at render time by
`app/services/website_data_providers.py`'s registry — see
tests/test_website_no_vertical_hardcoding.py, which this file's code must
never trip.

Security note (Phase 11 §13): `WebsiteSection.props`/`data_source` and
`WebsiteVersion.theme`/`navigation`/`seo_defaults` are JSONB columns, but they
are never trusted as stored — every write path validates the *assembled*
`WebsiteSpecification` (app/schemas/website_specification.py) before any row
is written or updated. The DB schema does not enforce that validation by
itself; `app/services/website_service.py` is the only intended write path.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin

# Same SQLite-fallback pattern as app/models/business_blueprint.py — plain
# JSON on SQLite (unit tests), JSONB (GIN-indexable) on real Postgres.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class WebsiteVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class Website(TenantScopedMixin, Base):
    """The per-tenant site container (design doc §1). Phase 11 enforces at
    most one `Website` row per tenant via a partial unique index — lifting
    that later (multi-site-per-tenant) is an additive follow-up, not a
    schema rewrite (design doc §18 "Deliberately deferred")."""

    __tablename__ = "websites"
    __table_args__ = (
        Index("uq_websites_one_per_tenant", "tenant_id", unique=True),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable — a Website need not be generated from a Blueprint (a tenant
    # could, in principle, build one by hand via the section-edit API).
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=True
    )
    current_published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("website_versions.id", use_alter=True, name="fk_websites_current_published"),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class WebsiteVersion(TenantScopedMixin, Base):
    """Full-row immutable-per-version envelope (design doc §2), mirroring
    `BusinessBlueprint`'s exact versioning shape. `status` transitions only
    DRAFT -> PUBLISHED -> SUPERSEDED (design doc §14); enforcement lives in
    `app/services/website_service.py`, never relaxed at the DB layer alone."""

    __tablename__ = "website_versions"
    __table_args__ = (
        UniqueConstraint("website_id", "version", name="uq_website_versions_website_version"),
        # At most one PUBLISHED version per website at a time (mirrors
        # BusinessBlueprint's "one ACTIVE per tenant" partial index).
        Index(
            "uq_website_versions_one_published_per_website",
            "website_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
            sqlite_where=text("status = 'PUBLISHED'"),
        ),
    )

    website_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("websites.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WebsiteVersionStatus.DRAFT, index=True
    )
    theme: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    navigation: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    seo_defaults: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    # Per-field provenance snapshot (design doc §10) — reuses
    # ClaimProvenance's exact 3-value vocabulary as plain strings (no FK;
    # this is a point-in-time snapshot, not a live reference).
    generation_provenance: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # The version this row supersedes, if any (mirrors
    # BusinessBlueprint.supersedes_id exactly).
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class WebsitePage(TenantScopedMixin, Base):
    """One page within a WebsiteVersion (design doc §3)."""

    __tablename__ = "website_pages"
    __table_args__ = (
        UniqueConstraint("website_version_id", "slug", name="uq_website_pages_version_slug"),
    )

    website_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("website_versions.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    seo: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    order_index: Mapped[int] = mapped_column(nullable=False, default=0)


class WebsiteSection(TenantScopedMixin, Base):
    """One content block within a WebsitePage (design doc §4).
    `component_type` is validated against the closed `ComponentType` enum
    (app/schemas/website_specification.py) by the service layer before a row
    is ever written — never trusted from the DB column alone."""

    __tablename__ = "website_sections"

    page_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("website_pages.id"), nullable=False, index=True
    )
    component_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    props: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    # Generic vertical-data-binding pointer (design doc §11/§12):
    # {"provider_key": "medical_tourism.provider_directory", "params": {...}}
    # or null for a section with no external data dependency.
    data_source: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    order_index: Mapped[int] = mapped_column(nullable=False, default=0)
