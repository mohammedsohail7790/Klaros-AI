"""Phase 13: customers.external_provider/external_id — mirrors the
existing Invoice.external_provider/external_id columns exactly. Tracks
which external accounting-system record (QuickBooks Online, ...) this
Klaros customer maps to, once a real sync creates one. Nullable/unset
until then; never fabricated.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch_op:
        batch_op.add_column(sa.Column("external_provider", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(255), nullable=True))
        batch_op.create_unique_constraint(
            "uq_customers_tenant_external_provider_id",
            ["tenant_id", "external_provider", "external_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_constraint("uq_customers_tenant_external_provider_id", type_="unique")
        batch_op.drop_column("external_id")
        batch_op.drop_column("external_provider")
