"""Email replies: reply routes and the replies themselves.

Additive only: two new tables. `inbound_emails` has row-level security on PostgreSQL (tenant_id = current_tenant_id()) like 0072-0075. `email_reply_routes` has NONE on purpose: a reply
arrives knowing only its unguessable token, so the token -> business lookup must work before any business is known.

Rollback: `alembic downgrade 0075_mt_revenue_terms` drops both tables (replies and routes are lost; export them first if they matter).

Revision ID: 0076_inbound_email
Revises: 0075_mt_revenue_terms
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076_inbound_email"
down_revision: Union[str, None] = "0075_mt_revenue_terms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "email_reply_routes", *_base(),
        sa.Column("token", sa.String(24), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", _UUID, nullable=False),
        sa.UniqueConstraint("tenant_id", "entity_type", "entity_id", name="uq_email_reply_route_entity"),
    )
    op.create_index("ix_email_reply_routes_token", "email_reply_routes", ["token"], unique=True)
    op.create_index("ix_email_reply_routes_tenant_id", "email_reply_routes", ["tenant_id"])
    op.create_table(
        "inbound_emails", *_base(),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", _UUID, nullable=False),
        sa.Column("message_id", sa.String(500), nullable=False),
        sa.Column("from_address", sa.String(320), nullable=False),
        sa.Column("from_name", sa.String(255), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_stored", sa.Boolean(), nullable=False),
        sa.Column("sender_matches", sa.Boolean(), nullable=False),
        sa.Column("spam_score", sa.Float(), nullable=True),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "message_id", name="uq_inbound_email_message"),
    )
    op.create_index("ix_inbound_emails_tenant_id", "inbound_emails", ["tenant_id"])
    op.create_index("ix_inbound_emails_entity_id", "inbound_emails", ["entity_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE inbound_emails ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_select ON inbound_emails FOR SELECT USING (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_insert ON inbound_emails FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
    op.execute("CREATE POLICY tenant_update ON inbound_emails FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy in ("tenant_select", "tenant_insert", "tenant_update"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON inbound_emails")
    op.drop_table("inbound_emails")
    op.drop_table("email_reply_routes")
