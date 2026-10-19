"""Integration catalog: Google Ads and Meta Ads are real now; WhatsApp and the bank feed are added.

Data only. Existing rows are updated in place (status and wording); the two new providers are inserted when missing. Rows an operator added by hand are untouched.

Revision ID: 0079_catalog_real_connectors
Revises: 0078_whatsapp_inbound
Create Date: 2026-10-19
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0079_catalog_real_connectors"
down_revision: Union[str, None] = "0078_whatsapp_inbound"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHANGED = ("google_ads", "meta_ads")
_NEW = ("whatsapp", "plaid")


def upgrade() -> None:
    from app.data.integration_provider_catalog_seed import SEED_PROVIDERS

    catalog = sa.table(
        "integration_provider_catalog",
        sa.column("id", sa.Uuid),
        sa.column("provider_key", sa.String),
        sa.column("display_name", sa.String),
        sa.column("category", sa.String),
        sa.column("implementation_status", sa.String),
        sa.column("auth_shape", sa.String),
        sa.column("description", sa.Text),
        sa.column("capabilities", sa.JSON),
        sa.column("recommended_for_verticals", sa.JSON),
        sa.column("health_check_strategy_ref", sa.String),
    )
    bind = op.get_bind()
    for p in SEED_PROVIDERS:
        key = p["provider_key"]
        if key in _CHANGED:
            op.execute(catalog.update().where(catalog.c.provider_key == key).values(implementation_status=str(p["implementation_status"]), description=p["description"]))
        elif key in _NEW:
            exists = bind.execute(sa.select(catalog.c.id).where(catalog.c.provider_key == key)).first()
            if exists is None:
                op.execute(catalog.insert().values(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"klaros:integration-provider-catalog:{key}"), provider_key=key, display_name=p["display_name"],
                    category=p["category"], implementation_status=str(p["implementation_status"]), auth_shape=str(p["auth_shape"]), description=p["description"],
                    capabilities=p["capabilities"], recommended_for_verticals=p["recommended_for_verticals"], health_check_strategy_ref=p["health_check_strategy_ref"],
                ))


def downgrade() -> None:
    catalog = sa.table("integration_provider_catalog", sa.column("provider_key", sa.String), sa.column("implementation_status", sa.String))
    op.execute(catalog.delete().where(catalog.c.provider_key.in_(_NEW)))
    op.execute(catalog.update().where(catalog.c.provider_key.in_(_CHANGED)).values(implementation_status="STUB"))
