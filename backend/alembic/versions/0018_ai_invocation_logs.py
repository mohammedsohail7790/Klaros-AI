"""Phase 12E: ai_invocation_logs — real audit + usage trail for direct
AI-provider calls (distinct from audit_logs, which records TOOL
executions). Never stores the API key, raw prompt, or raw response — only
safe metadata (provider/model/operation/timing/token counts/error
classification).

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_invocation_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_type", sa.String(50), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("estimated_cost_usd", sa.String(20), nullable=True),
        sa.Column("input_metadata", sa.JSON, nullable=True),
        sa.Column("output_metadata", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ai_invocation_logs_tenant_id", "ai_invocation_logs", ["tenant_id"])
    op.create_index("ix_ai_invocation_logs_correlation_id", "ai_invocation_logs", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_invocation_logs_correlation_id", table_name="ai_invocation_logs")
    op.drop_index("ix_ai_invocation_logs_tenant_id", table_name="ai_invocation_logs")
    op.drop_table("ai_invocation_logs")
