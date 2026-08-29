"""approval orchestration: approval_requests gains requested_by_role,
decided_at, execution_status, execution_result, execution_error,
executed_at, execution_attempts, idempotency_key — so approving a request
can resume and execute the original tool call, not just record a decision.
Also adds morning_brief_recommendations.approval_request_id so a
recommendation whose Execute hit an APPROVAL_REQUIRED tool links straight
to the real approval it created.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("approval_requests", sa.Column("requested_by_role", sa.String(30), nullable=True))
    op.add_column("approval_requests", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "approval_requests",
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="NOT_STARTED"),
    )
    op.add_column("approval_requests", sa.Column("execution_result", sa.JSON, nullable=True))
    op.add_column("approval_requests", sa.Column("execution_error", sa.String(2000), nullable=True))
    op.add_column("approval_requests", sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "approval_requests",
        sa.Column("execution_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("approval_requests", sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.create_index("ix_approval_requests_execution_status", "approval_requests", ["execution_status"])


def downgrade() -> None:
    op.drop_index("ix_approval_requests_execution_status", table_name="approval_requests")
    op.drop_column("approval_requests", "idempotency_key")
    op.drop_column("approval_requests", "execution_attempts")
    op.drop_column("approval_requests", "executed_at")
    op.drop_column("approval_requests", "execution_error")
    op.drop_column("approval_requests", "execution_result")
    op.drop_column("approval_requests", "execution_status")
    op.drop_column("approval_requests", "decided_at")
    op.drop_column("approval_requests", "requested_by_role")
