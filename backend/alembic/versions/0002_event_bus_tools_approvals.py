"""events, event_processing_records, dead_letter_events, approval_requests, notifications

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
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
    op.create_table(
        "events",
        *_timestamp_cols(),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=True),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PUBLISHED"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_events_tenant_idempotency_key"
        ),
    )
    op.create_index("ix_events_tenant_id", "events", ["tenant_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"])

    op.create_table(
        "event_processing_records",
        *_timestamp_cols(),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("handler_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.UniqueConstraint(
            "event_id", "handler_name", name="uq_event_processing_event_handler"
        ),
    )
    op.create_index("ix_event_processing_records_tenant_id", "event_processing_records", ["tenant_id"])
    op.create_index("ix_event_processing_records_event_id", "event_processing_records", ["event_id"])

    op.create_table(
        "dead_letter_events",
        *_timestamp_cols(),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("handler_name", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("replayed", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_dead_letter_events_tenant_id", "dead_letter_events", ["tenant_id"])
    op.create_index("ix_dead_letter_events_event_id", "dead_letter_events", ["event_id"])

    op.create_table(
        "approval_requests",
        *_timestamp_cols(),
        sa.Column("requested_by_type", sa.String(20), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("tool_input", sa.JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.String(2000), nullable=True),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_approval_requests_tenant_id", "approval_requests", ["tenant_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])

    op.create_table(
        "notifications",
        *_timestamp_cols(),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="INFO"),
        sa.Column("category", sa.String(100), nullable=False, server_default="general"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("approval_requests")
    op.drop_table("dead_letter_events")
    op.drop_table("event_processing_records")
    op.drop_table("events")
