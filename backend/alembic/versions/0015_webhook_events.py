"""Phase 12C: webhook_events — idempotency + audit ledger for inbound
provider webhooks (Stripe, Twilio, ...). The (provider, external_event_id)
unique constraint is what makes duplicate webhook delivery a real DB-enforced
no-op rather than an application-level convention.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="RECEIVED"),
        sa.Column("raw_payload", sa.JSON, nullable=False),
        sa.Column("error_detail", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_webhook_events_provider_external_id"),
    )
    op.create_index("ix_webhook_events_provider", "webhook_events", ["provider"])
    op.create_index("ix_webhook_events_tenant_id", "webhook_events", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_events_tenant_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider", table_name="webhook_events")
    op.drop_table("webhook_events")
