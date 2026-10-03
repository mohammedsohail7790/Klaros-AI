"""Klaros <-> Halla integration: `leads.external_provider` / `leads.external_id`.

The link from a Klaros lead to the same lead in an external system that works on it (the AI
workforce platform). Mirrors `customers` and `appointments`, which already carry the same two
nullable columns and the same per-tenant unique constraint. No data is changed; Klaros' own lead id
remains the lead's identity. RLS on `leads` is unchanged (the table is already RLS-enforced).

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table: same pattern as 0019 (customers) — a plain ALTER ADD CONSTRAINT is not valid on SQLite.
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("external_provider", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(255), nullable=True))
        batch_op.create_unique_constraint(
            "uq_leads_tenant_external_provider_id", ["tenant_id", "external_provider", "external_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_constraint("uq_leads_tenant_external_provider_id", type_="unique")
        batch_op.drop_column("external_id")
        batch_op.drop_column("external_provider")
