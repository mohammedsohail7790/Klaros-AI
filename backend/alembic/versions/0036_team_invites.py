"""Team invites — a real, DB-backed pending-invitation record so a
tenant can list/revoke outstanding invites, not just a bare stateless
token. See app/models/user.py::TeamInvite.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_invites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("invited_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_team_invites_tenant_id", "team_invites", ["tenant_id"])
    op.create_index("ix_team_invites_email", "team_invites", ["email"])


def downgrade() -> None:
    op.drop_index("ix_team_invites_email", table_name="team_invites")
    op.drop_index("ix_team_invites_tenant_id", table_name="team_invites")
    op.drop_table("team_invites")
