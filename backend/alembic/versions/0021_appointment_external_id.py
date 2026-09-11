"""Phase 14: appointments.external_provider/external_id — mirrors the
existing Invoice/Customer external_provider/external_id columns exactly.
Tracks which external calendar's event (Google Calendar, ...) this
Klaros appointment maps to, once a real sync creates one. Nullable/unset
until then; never fabricated.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.add_column(sa.Column("external_provider", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(255), nullable=True))
        batch_op.create_unique_constraint(
            "uq_appointments_tenant_external_provider_id",
            ["tenant_id", "external_provider", "external_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.drop_constraint("uq_appointments_tenant_external_provider_id", type_="unique")
        batch_op.drop_column("external_id")
        batch_op.drop_column("external_provider")
