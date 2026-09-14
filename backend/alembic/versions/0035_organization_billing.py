"""Add Organization billing fields — stripe_customer_id, stripe_subscription_id,
trial_ends_at, current_period_end. Klaros's own SaaS subscription billing
(Organization.plan/billing_status already existed as columns but were never
read anywhere; this migration adds the remaining state those two fields
now become real for). See app/models/organization.py and
app/services/billing_service.py.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    op.add_column("organizations", sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True))
    op.add_column("organizations", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "current_period_end")
    op.drop_column("organizations", "trial_ends_at")
    op.drop_column("organizations", "stripe_subscription_id")
    op.drop_column("organizations", "stripe_customer_id")
