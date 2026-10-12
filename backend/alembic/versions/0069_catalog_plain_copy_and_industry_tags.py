"""Integration catalog: plain-language descriptions and industry tags.

Data only. The catalog descriptions are shown to business owners on the
Integrations page, but several were written as engineering notes (file paths,
adapter error strings). They are replaced with what the integration does for the
business. Home-services providers are also tagged in `recommended_for_verticals`
so they stop being suggested to a business running a different industry module.

Rows an operator added by hand (not in the seed) are left untouched.

Revision ID: 0069_catalog_plain_copy
Revises: 0068_account_lifecycle
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0069_catalog_plain_copy"
down_revision: Union[str, None] = "0068_account_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.data.integration_provider_catalog_seed import SEED_PROVIDERS

    catalog = sa.table(
        "integration_provider_catalog",
        sa.column("provider_key", sa.String),
        sa.column("description", sa.Text),
        sa.column("recommended_for_verticals", sa.JSON),
    )
    for p in SEED_PROVIDERS:
        op.execute(
            catalog.update()
            .where(catalog.c.provider_key == p["provider_key"])
            .values(description=p["description"], recommended_for_verticals=p["recommended_for_verticals"])
        )


def downgrade() -> None:
    # Copy and tags only: the previous wording is not worth restoring, and
    # clearing the tags returns matching to "every provider suits everyone".
    catalog = sa.table(
        "integration_provider_catalog",
        sa.column("recommended_for_verticals", sa.JSON),
    )
    op.execute(catalog.update().values(recommended_for_verticals=[]))
