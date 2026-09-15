"""AI kill switch — a real, org-wide emergency flag that instantly
blocks every AI/automation-initiated tool execution (never a human's own
direct action) until an owner turns it back on. See
app/models/organization.py and app/tools/registry.py.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("ai_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("organizations", sa.Column("ai_paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("ai_paused_by", sa.Uuid(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "ai_paused_by")
    op.drop_column("organizations", "ai_paused_at")
    op.drop_column("organizations", "ai_paused")
