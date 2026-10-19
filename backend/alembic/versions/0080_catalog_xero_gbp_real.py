"""Integration catalog: Xero and Google Business Profile are real now.

Data only: status and plain-language description for the two providers. Rows an operator added by hand are untouched.

Revision ID: 0080_catalog_xero_gbp_real
Revises: 0079_catalog_real_connectors
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0080_catalog_xero_gbp_real"
down_revision: Union[str, None] = "0079_catalog_real_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = ("xero", "google_business_profile")


def upgrade() -> None:
    from app.data.integration_provider_catalog_seed import SEED_PROVIDERS

    catalog = sa.table(
        "integration_provider_catalog",
        sa.column("provider_key", sa.String),
        sa.column("implementation_status", sa.String),
        sa.column("description", sa.Text),
    )
    for p in SEED_PROVIDERS:
        if p["provider_key"] in _KEYS:
            op.execute(catalog.update().where(catalog.c.provider_key == p["provider_key"]).values(implementation_status=str(p["implementation_status"]), description=p["description"]))


def downgrade() -> None:
    catalog = sa.table("integration_provider_catalog", sa.column("provider_key", sa.String), sa.column("implementation_status", sa.String))
    op.execute(catalog.update().where(catalog.c.provider_key.in_(_KEYS)).values(implementation_status="STUB"))
