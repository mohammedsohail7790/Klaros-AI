"""leads, customers, customer_notes, appointments, communication_logs

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
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
        "customers",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("phone_normalized", sa.String(50), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(120), nullable=True),
        sa.Column("postal_code", sa.String(20), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PROSPECT"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_index("ix_customers_email", "customers", ["email"])
    op.create_index("ix_customers_phone_normalized", "customers", ["phone_normalized"])

    op.create_table(
        "leads",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("phone_normalized", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_detail", sa.String(255), nullable=True),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("service_requested", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("urgency", sa.String(20), nullable=False, server_default="MEDIUM"),
        sa.Column("estimated_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="NEW"),
        sa.Column("lead_score", sa.Integer, nullable=True),
        sa.Column("score_version", sa.String(20), nullable=True),
        sa.Column("score_reason", sa.Text, nullable=True),
        sa.Column("qualification_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("assigned_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_leads_tenant_idempotency_key"),
    )
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("ix_leads_customer_id", "leads", ["customer_id"])
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_index("ix_leads_phone_normalized", "leads", ["phone_normalized"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_idempotency_key", "leads", ["idempotency_key"])

    op.create_table(
        "customer_notes",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("author_type", sa.String(20), nullable=False),
        sa.Column("author_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text, nullable=False),
    )
    op.create_index("ix_customer_notes_tenant_id", "customer_notes", ["tenant_id"])
    op.create_index("ix_customer_notes_customer_id", "customer_notes", ["customer_id"])

    op.create_table(
        "appointments",
        *_timestamp_cols(),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assigned_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("service", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="TENTATIVE"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_appointments_tenant_idempotency_key"
        ),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"])
    op.create_index("ix_appointments_lead_id", "appointments", ["lead_id"])
    op.create_index("ix_appointments_customer_id", "appointments", ["customer_id"])
    op.create_index("ix_appointments_start_time", "appointments", ["start_time"])

    op.create_table(
        "communication_logs",
        *_timestamp_cols(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("template", sa.String(100), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
    )
    op.create_index("ix_communication_logs_tenant_id", "communication_logs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("communication_logs")
    op.drop_table("appointments")
    op.drop_table("customer_notes")
    op.drop_table("leads")
    op.drop_table("customers")
