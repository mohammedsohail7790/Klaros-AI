"""Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2, KLAROS_FINAL_INTEGRATION_MODEL.md):
`IntegrationProviderCatalog` — tenant-independent reference data describing
what a provider IS and CAN DO. This is explicitly NOT the same concept as
the existing `IntegrationConnection` (app/models/integration.py), which
remains the sole runtime source of a tenant's own per-provider credential/
connection state. The two must never be merged:

  - `IntegrationProviderCatalog.implementation_status` (this file): platform-
    level, tenant-independent — REAL / STUB / WEBHOOK_NORMALIZER, matching
    verified adapter reality in app/integrations/adapters.py. A STUB row
    must never be presented as a live production integration (see
    KLAROS_DO_NOT_BUILD_YET.md §9) — this is enforced at the service layer
    by intersecting this status with a tenant's own `IntegrationConnection.
    status`, never by collapsing the two enums into one field.
  - `IntegrationConnection.status` (existing, unchanged): per-tenant,
    runtime — NOT_CONNECTED / CONNECTING / CONNECTED / ERROR / DISCONNECTED.

Written only by platform curation (`MANAGE_INTEGRATIONS_CATALOG`
permission, see app/models/rbac.py) — not by the existing, tenant-scoped
`MANAGE_INTEGRATIONS` permission that gates a tenant's own connect/
disconnect actions.
"""

from enum import StrEnum

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProviderImplementationStatus(StrEnum):
    """Platform-level, tenant-independent — distinct from
    app.models.integration.ConnectionStatus (per-tenant runtime state)."""

    REAL = "REAL"
    STUB = "STUB"
    WEBHOOK_NORMALIZER = "WEBHOOK_NORMALIZER"


class ProviderAuthShape(StrEnum):
    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    WEBHOOK = "WEBHOOK"


class IntegrationProviderCatalog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-independent reference data — deliberately NOT `TenantScopedMixin`.
    Every tenant reads the same catalog; only `IntegrationConnection` (a
    tenant's own connection to a `provider_key`) is tenant-scoped."""

    __tablename__ = "integration_provider_catalog"

    # Matches the string used as `IntegrationConnection.provider` /
    # app.integrations.adapters `provider_name` — the join key between
    # catalog reference data and a tenant's own runtime connection, e.g.
    # "stripe", "quickbooks", "google_calendar".
    provider_key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    implementation_status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    auth_shape: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Array of VerticalExtension.key strings this provider is recommended
    # for — data a vertical's future Recommendation-Engine plugin function
    # reads, never a hardcoded per-provider/per-vertical branch anywhere in
    # this codebase.
    recommended_for_verticals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    health_check_strategy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Phase 3 (Recommendation Engine, alembic/versions/0044_recommendation_
    # engine.py): array of generic capability-key strings this provider can
    # satisfy (e.g. "payment_processing", "appointment_scheduling") — the
    # smallest additive extension of this existing registry needed for
    # deterministic capability->provider matching, deliberately NOT a new
    # `Capability` table (no consumer besides this tag exists, and the
    # Recommendation Engine's task brief explicitly warns against
    # duplicating registry metadata into a new model). Purely descriptive
    # reference data — matched by simple membership, never a vertical
    # branch.
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
