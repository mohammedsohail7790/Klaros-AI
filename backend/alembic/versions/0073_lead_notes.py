"""Lead notes: freeform notes on an enquiry, before it is (or without it ever becoming) a customer.

Until now a note could only be attached to a customer, so a new enquiry had nowhere for a coordinator or an AI crew member to record an assessment. Additive only: one new table, nothing
existing is altered. Row-level security on PostgreSQL as in 0066/0071/0072 (tenant_id = current_tenant_id()).

Rollback: `alembic downgrade 0072_tenant_settings` drops the table and its policies (the notes are lost; export them first if they matter).

Revision ID: 0073_lead_notes
Revises: 0072_tenant_settings
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073_lead_notes"
down_revision: Union[str, None] = "0072_tenant_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "lead_notes",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("lead_id", _UUID, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("author_type", sa.String(20), nullable=False),
        sa.Column("author_id", _UUID, nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_lead_notes_tenant_id", "lead_notes", ["tenant_id"])
    op.create_index("ix_lead_notes_lead_id", "lead_notes", ["lead_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE lead_notes ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON lead_notes FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON lead_notes FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON lead_notes")
    op.drop_index("ix_lead_notes_lead_id", table_name="lead_notes")
    op.drop_index("ix_lead_notes_tenant_id", table_name="lead_notes")
    op.drop_table("lead_notes")
