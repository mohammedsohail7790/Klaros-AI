"""Add Organization.timezone — the tenant's general business timezone, used
by the Automation Engine's SCHEDULE trigger to resolve local time-of-day.
See app/models/organization.py.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "timezone")
