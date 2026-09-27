"""Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1, KLAROS_FINAL_DOMAIN_MODEL.md
"DomainDefinition (= VerticalExtension, same entity, name reconciled)"):
the domain-extensibility registry.

This is the mechanism, not a placeholder for one: core services
(`crm_tools.py`, `job_tools.py`, `invoice_tools.py`, ...) must never branch
on a vertical name (`if business_type == "medical_tourism"`). A vertical
plugs in by existing as a row here — enabling/disabling a vertical for an
organization is a data change (`OrganizationVerticalExtension`), never a
code deploy or an `if` statement. Phase 1 ships only the registry itself;
nothing in the codebase queries it yet (no Discovery/Blueprint/
Recommendation/Agent consumer exists until later phases) — that absence is
intentional and matches the Phase 1 scope boundary exactly.

`VerticalExtension` is tenant-independent reference data (platform-curated
list of verticals that exist at all — "medical_tourism", "dropshipping",
...), analogous to `IntegrationProviderCatalog`. `OrganizationVerticalExtension`
is the tenant-scoped join row recording that a specific organization has
opted into a specific vertical.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class VerticalExtensionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BETA = "BETA"
    DISABLED = "DISABLED"


class VerticalExtension(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-independent reference data — deliberately NOT `TenantScopedMixin`.
    Every tenant sees the same set of registered verticals; only
    `OrganizationVerticalExtension` (below) is tenant-scoped. Written only by
    platform curation (no Phase 1 API surface writes this table outside the
    seed migration — see KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1, "Files/
    subsystems affected: new models/migration only")."""

    __tablename__ = "vertical_extensions"

    # Stable machine key other code/data reference (e.g.
    # `recommended_for_verticals` tags on IntegrationProviderCatalog rows) —
    # e.g. "medical_tourism", "dropshipping". Never branched on in core
    # service code; only ever used as a registry lookup key or stored as
    # opaque tag data.
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VerticalExtensionStatus.BETA, index=True
    )
    # List of capability keys this vertical contributes (e.g.
    # ["medical_tourism.patient_leads", "medical_tourism.provider_directory"]).
    # Purely descriptive registry data in Phase 1 — no consumer reads this
    # yet; the Recommendation Engine (a later phase) is the first intended
    # reader, via a plugin function keyed by `key`, never a hardcoded switch.
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Optional JSON Schema describing configuration this vertical accepts
    # when an organization enables it (e.g. default currency, region). Not
    # required in Phase 1; left nullable so the registry mechanism can be
    # exercised (seeded + tested) before any vertical actually needs
    # configuration.
    configuration_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OrganizationVerticalExtension(TenantScopedMixin, Base):
    """Tenant-scoped join row: organization X has enabled vertical Y.
    RLS audit-mode instrumentation applied in the same migration that
    creates this table (see alembic/versions/0041_vertical_extension_registry.py)
    — matching KLAROS_FINAL_SECURITY_MODEL.md §D's "new tables RLS-on-day-one"
    rule, at the same audit-mode maturity level Phase 0 established (not
    full FORCE enforcement, which the wider rollout hasn't reached yet)."""

    __tablename__ = "organization_vertical_extensions"

    vertical_extension_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("vertical_extensions.id"), nullable=False, index=True
    )
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "vertical_extension_id", name="uq_org_vertical_extension_tenant_vertical"
        ),
    )
