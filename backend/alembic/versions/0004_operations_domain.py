"""jobs, workers, tasks, attachments, materials, purchase orders, scope
changes, exceptions, QA, completion packets, customer signoffs

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
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
        "workers",
        *_timestamp_cols(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="AVAILABLE"),
        sa.Column("skills", sa.JSON, nullable=False),
        sa.Column("service_types", sa.JSON, nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_workers_tenant_id", "workers", ["tenant_id"])

    op.create_table(
        "jobs",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("appointment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("job_number", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("service_type", sa.String(120), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="NORMAL"),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("assigned_team_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer, nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_revenue", sa.Numeric(12, 2), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("estimated_margin", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_revenue", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_margin", sa.Numeric(12, 2), nullable=True),
        sa.Column("customer_notes", sa.Text, nullable=True),
        sa.Column("internal_notes", sa.Text, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_idempotency_key"),
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])
    op.create_index("ix_jobs_job_number", "jobs", ["job_number"])
    op.create_index("ix_jobs_customer_id", "jobs", ["customer_id"])
    op.create_index("ix_jobs_appointment_id", "jobs", ["appointment_id"])
    op.create_index("ix_jobs_assigned_user_id", "jobs", ["assigned_user_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_priority", "jobs", ["priority"])
    op.create_index("ix_jobs_scheduled_start", "jobs", ["scheduled_start"])
    op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"])

    op.create_table(
        "job_tasks",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("required", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("assigned_to", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_job_tasks_tenant_id", "job_tasks", ["tenant_id"])
    op.create_index("ix_job_tasks_job_id", "job_tasks", ["job_id"])

    op.create_table(
        "job_attachments",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("storage_provider", sa.String(50), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("transcription_status", sa.String(20), nullable=True),
        sa.Column("transcript", sa.Text, nullable=True),
    )
    op.create_index("ix_job_attachments_tenant_id", "job_attachments", ["tenant_id"])
    op.create_index("ix_job_attachments_job_id", "job_attachments", ["job_id"])
    op.create_index("ix_job_attachments_kind", "job_attachments", ["kind"])

    op.create_table(
        "job_materials",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("estimated_unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("supplier", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="REQUIRED"),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_job_materials_tenant_id", "job_materials", ["tenant_id"])
    op.create_index("ix_job_materials_job_id", "job_materials", ["job_id"])

    op.create_table(
        "purchase_orders",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("supplier", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("provider", sa.String(50), nullable=False, server_default="internal_draft"),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])
    op.create_index("ix_purchase_orders_job_id", "purchase_orders", ["job_id"])

    op.create_table(
        "purchase_order_items",
        *_timestamp_cols(),
        sa.Column("purchase_order_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_material_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("estimated_unit_cost", sa.Numeric(12, 2), nullable=True),
    )
    op.create_index("ix_purchase_order_items_tenant_id", "purchase_order_items", ["tenant_id"])
    op.create_index(
        "ix_purchase_order_items_purchase_order_id", "purchase_order_items", ["purchase_order_id"]
    )

    op.create_table(
        "scope_changes",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("estimated_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("estimated_revenue", sa.Numeric(12, 2), nullable=True),
        sa.Column("margin_impact", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DETECTED"),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_scope_changes_tenant_id", "scope_changes", ["tenant_id"])
    op.create_index("ix_scope_changes_job_id", "scope_changes", ["job_id"])

    op.create_table(
        "operations_exceptions",
        *_timestamp_cols(),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("recommended_action", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("assigned_to", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "type", "entity_id", "status", name="uq_exceptions_tenant_type_entity_open"
        ),
    )
    op.create_index("ix_operations_exceptions_tenant_id", "operations_exceptions", ["tenant_id"])
    op.create_index("ix_operations_exceptions_type", "operations_exceptions", ["type"])
    op.create_index("ix_operations_exceptions_severity", "operations_exceptions", ["severity"])
    op.create_index("ix_operations_exceptions_entity_id", "operations_exceptions", ["entity_id"])
    op.create_index("ix_operations_exceptions_status", "operations_exceptions", ["status"])

    op.create_table(
        "job_qa",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="NOT_STARTED"),
        sa.Column("checks", sa.JSON, nullable=False),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("performed_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("job_id", name="uq_job_qa_job_id"),
    )
    op.create_index("ix_job_qa_tenant_id", "job_qa", ["tenant_id"])
    op.create_index("ix_job_qa_job_id", "job_qa", ["job_id"])

    op.create_table(
        "completion_packets",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("summary", sa.JSON, nullable=False),
        sa.UniqueConstraint("job_id", name="uq_completion_packets_job_id"),
    )
    op.create_index("ix_completion_packets_tenant_id", "completion_packets", ["tenant_id"])
    op.create_index("ix_completion_packets_job_id", "completion_packets", ["job_id"])

    op.create_table(
        "customer_signoffs",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signed_by", sa.String(255), nullable=False),
        sa.Column("signature_reference", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="internal_signoff"),
    )
    op.create_index("ix_customer_signoffs_tenant_id", "customer_signoffs", ["tenant_id"])
    op.create_index("ix_customer_signoffs_job_id", "customer_signoffs", ["job_id"])


def downgrade() -> None:
    op.drop_table("customer_signoffs")
    op.drop_table("completion_packets")
    op.drop_table("job_qa")
    op.drop_table("operations_exceptions")
    op.drop_table("scope_changes")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("job_materials")
    op.drop_table("job_attachments")
    op.drop_table("job_tasks")
    op.drop_table("jobs")
    op.drop_table("workers")
