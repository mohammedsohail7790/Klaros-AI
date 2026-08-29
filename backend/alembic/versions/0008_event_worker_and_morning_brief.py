"""event worker observability columns (event_processing_records.
last_attempt_at/processed_at, dead_letter_events.replayed_at), morning brief
scheduling columns on organizations, and morning_briefs/
morning_brief_insights/morning_brief_recommendations tables

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_cols():
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
    ]


def upgrade() -> None:
    # --- Event worker observability (additive, nullable — existing rows are
    # still valid with these unset) ---
    op.add_column(
        "event_processing_records",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "event_processing_records",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dead_letter_events",
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Morning Brief scheduling (additive, defaulted so existing
    # organizations keep working with the feature disabled) ---
    op.add_column(
        "organizations",
        sa.Column("morning_brief_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "organizations",
        sa.Column("morning_brief_local_time", sa.String(5), nullable=False, server_default="07:00"),
    )
    op.add_column(
        "organizations",
        sa.Column("morning_brief_timezone", sa.String(50), nullable=False, server_default="UTC"),
    )

    # --- Morning Brief tables ---
    op.create_table(
        "morning_briefs",
        *_timestamp_cols(),
        sa.Column("brief_date", sa.Date, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="DETERMINISTIC"),
        sa.Column("generated_by", sa.String(20), nullable=False),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("source_data", sa.JSON, nullable=False),
    )
    op.create_index("ix_morning_briefs_tenant_id", "morning_briefs", ["tenant_id"])
    op.create_index("ix_morning_briefs_brief_date", "morning_briefs", ["brief_date"])

    op.create_table(
        "morning_brief_insights",
        *_timestamp_cols(),
        sa.Column("brief_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False, server_default="LOW"),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("related_entity_type", sa.String(50), nullable=True),
        sa.Column("related_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source_tool", sa.String(100), nullable=True),
    )
    op.create_index("ix_morning_brief_insights_tenant_id", "morning_brief_insights", ["tenant_id"])
    op.create_index("ix_morning_brief_insights_brief_id", "morning_brief_insights", ["brief_id"])

    op.create_table(
        "morning_brief_recommendations",
        *_timestamp_cols(),
        sa.Column("brief_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("what", sa.String(500), nullable=False),
        sa.Column("why", sa.String(1000), nullable=False),
        sa.Column("related_entity_type", sa.String(50), nullable=True),
        sa.Column("related_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("next_action", sa.String(500), nullable=False),
        sa.Column("executable_tool", sa.String(100), nullable=True),
        sa.Column("executable_input", sa.JSON, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
    )
    op.create_index("ix_morning_brief_recommendations_tenant_id", "morning_brief_recommendations", ["tenant_id"])
    op.create_index("ix_morning_brief_recommendations_brief_id", "morning_brief_recommendations", ["brief_id"])


def downgrade() -> None:
    op.drop_table("morning_brief_recommendations")
    op.drop_table("morning_brief_insights")
    op.drop_table("morning_briefs")
    op.drop_column("organizations", "morning_brief_timezone")
    op.drop_column("organizations", "morning_brief_local_time")
    op.drop_column("organizations", "morning_brief_enabled")
    op.drop_column("dead_letter_events", "replayed_at")
    op.drop_column("event_processing_records", "processed_at")
    op.drop_column("event_processing_records", "last_attempt_at")
