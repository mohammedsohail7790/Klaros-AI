"""morning brief AI provider attribution: morning_briefs gains ai_provider,
ai_model, ai_generation_ms — set only when a real provider call actually
succeeded and validated (Phase 9B), always NULL for a DETERMINISTIC brief.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("morning_briefs", sa.Column("ai_provider", sa.String(30), nullable=True))
    op.add_column("morning_briefs", sa.Column("ai_model", sa.String(100), nullable=True))
    op.add_column("morning_briefs", sa.Column("ai_generation_ms", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("morning_briefs", "ai_generation_ms")
    op.drop_column("morning_briefs", "ai_model")
    op.drop_column("morning_briefs", "ai_provider")
