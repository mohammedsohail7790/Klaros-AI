"""Adds the missing morning_brief_recommendations.approval_request_id
column. Migration 0009's docstring already claimed this column ("Also
adds morning_brief_recommendations.approval_request_id so a
recommendation whose Execute hit an APPROVAL_REQUIRED tool links
straight to the real approval it created") and the ORM model
(app/models/morning_brief.py) has declared it ever since, but the actual
`op.add_column` call was never written into 0009's upgrade() body — a
genuine authoring gap invisible to the test suite, which builds its
schema via Base.metadata.create_all() rather than real Alembic
migrations. Discovered by running the actual migration chain against a
real (if SQLite) database for the first time and exercising the
morning-brief generation path, which fails with `OperationalError: table
morning_brief_recommendations has no column named approval_request_id`
without it. Fixed forward here rather than editing the already-applied
0009, per this project's migration-history convention.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "morning_brief_recommendations",
        sa.Column("approval_request_id", sa.Uuid(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("morning_brief_recommendations", "approval_request_id")
