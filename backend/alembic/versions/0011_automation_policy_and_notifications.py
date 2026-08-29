"""Phase 10: per-tenant automation policy + notification orchestration.

Creates `tenant_tool_policies` (per-tenant ActionPolicy overrides, unique on
tenant_id+tool_name) and `notification_preferences` (per-user, per-type,
per-channel opt-in). Extends `notifications` in place — recipient_id, type,
priority, entity_type, entity_id, channel, status, dedupe_key, sent_at,
error — plus a unique constraint on (tenant_id, dedupe_key) for
notification idempotency (NULL dedupe_key rows, including every
pre-Phase-10 row, are never deduplicated against each other).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_tool_policies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(150), nullable=False),
        sa.Column("policy", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("configured_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "tool_name", name="uq_tenant_tool_policy"),
    )
    op.create_index("ix_tenant_tool_policies_tenant_id", "tenant_tool_policies", ["tenant_id"])
    op.create_index("ix_tenant_tool_policies_tool_name", "tenant_tool_policies", ["tool_name"])

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "user_id", "type", "channel", name="uq_notification_preference"),
    )
    op.create_index("ix_notification_preferences_tenant_id", "notification_preferences", ["tenant_id"])
    op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"])

    # `batch_alter_table` — required for SQLite (no native ALTER-based ADD
    # CONSTRAINT; Alembic falls back to its copy-and-move batch strategy),
    # and translates to direct, efficient ALTER statements on Postgres.
    # Found by actually running `alembic upgrade head` against a genuinely
    # fresh database during the Phase 11 production audit — every prior
    # dev.db update in this project used ad-hoc raw SQL against sqlite
    # directly, so this migration's own SQLite-incompatibility had never
    # been exercised before.
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(sa.Column("recipient_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("type", sa.String(40), nullable=False, server_default="GENERAL"))
        batch_op.add_column(sa.Column("priority", sa.String(10), nullable=False, server_default="MEDIUM"))
        batch_op.add_column(sa.Column("entity_type", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("channel", sa.String(10), nullable=False, server_default="IN_APP"))
        batch_op.add_column(sa.Column("status", sa.String(15), nullable=False, server_default="SENT"))
        batch_op.add_column(sa.Column("dedupe_key", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("error", sa.String(500), nullable=True))
        batch_op.create_unique_constraint("uq_notification_dedupe", ["tenant_id", "dedupe_key"])


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_constraint("uq_notification_dedupe", type_="unique")
        batch_op.drop_column("error")
        batch_op.drop_column("sent_at")
        batch_op.drop_column("dedupe_key")
        batch_op.drop_column("status")
        batch_op.drop_column("channel")
        batch_op.drop_column("entity_id")
        batch_op.drop_column("entity_type")
        batch_op.drop_column("priority")
        batch_op.drop_column("type")
        batch_op.drop_column("recipient_id")

    op.drop_index("ix_notification_preferences_user_id", table_name="notification_preferences")
    op.drop_index("ix_notification_preferences_tenant_id", table_name="notification_preferences")
    op.drop_table("notification_preferences")

    op.drop_index("ix_tenant_tool_policies_tool_name", table_name="tenant_tool_policies")
    op.drop_index("ix_tenant_tool_policies_tenant_id", table_name="tenant_tool_policies")
    op.drop_table("tenant_tool_policies")
