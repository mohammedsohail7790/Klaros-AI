"""Phase 1.2 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2): IntegrationProviderCatalog
— tenant-independent reference data (no tenant_id, no RLS, same treatment as
`vertical_extensions`). Seed data lives in
app/data/integration_provider_catalog_seed.py (SEED_PROVIDERS), the single
source of truth this migration and
tests/test_integration_provider_catalog.py's seed-data-matches-verified-
reality regression guard both read, so they can never drift from each
other. See that module's docstring for the verified real/stub/
webhook-normalizer status list.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-23

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_provider_catalog",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider_key", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("implementation_status", sa.String(20), nullable=False),
        sa.Column("auth_shape", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("recommended_for_verticals", sa.JSON(), nullable=False),
        sa.Column("health_check_strategy_ref", sa.String(255), nullable=True),
        sa.UniqueConstraint("provider_key", name="uq_integration_provider_catalog_provider_key"),
    )
    op.create_index("ix_integration_provider_catalog_provider_key", "integration_provider_catalog", ["provider_key"])
    op.create_index("ix_integration_provider_catalog_category", "integration_provider_catalog", ["category"])
    op.create_index(
        "ix_integration_provider_catalog_implementation_status",
        "integration_provider_catalog",
        ["implementation_status"],
    )

    catalog = sa.table(
        "integration_provider_catalog",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("provider_key", sa.String),
        sa.column("display_name", sa.String),
        sa.column("category", sa.String),
        sa.column("implementation_status", sa.String),
        sa.column("auth_shape", sa.String),
        sa.column("description", sa.Text),
        sa.column("recommended_for_verticals", sa.JSON),
        sa.column("health_check_strategy_ref", sa.String),
    )

    from app.data.integration_provider_catalog_seed import SEED_PROVIDERS

    op.bulk_insert(
        catalog,
        [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"klaros:integration-provider-catalog:{p['provider_key']}"),
                "provider_key": p["provider_key"],
                "display_name": p["display_name"],
                "category": p["category"],
                "implementation_status": str(p["implementation_status"]),
                "auth_shape": str(p["auth_shape"]),
                "description": p["description"],
                "recommended_for_verticals": p["recommended_for_verticals"],
                "health_check_strategy_ref": p["health_check_strategy_ref"],
            }
            for p in SEED_PROVIDERS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_integration_provider_catalog_implementation_status", table_name="integration_provider_catalog")
    op.drop_index("ix_integration_provider_catalog_category", table_name="integration_provider_catalog")
    op.drop_index("ix_integration_provider_catalog_provider_key", table_name="integration_provider_catalog")
    op.drop_table("integration_provider_catalog")
