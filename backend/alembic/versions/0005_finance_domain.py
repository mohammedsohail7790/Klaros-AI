"""invoices, invoice line items, payments, payment allocations, refunds,
credit notes, write-offs, job costs, vendors, vendor bills, payouts, cash
forecasts, collection actions; organizations.manual_starting_cash

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
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
    op.add_column("organizations", sa.Column("manual_starting_cash", sa.Numeric(14, 2), nullable=True))

    op.create_table(
        "invoices",
        *_timestamp_cols(),
        sa.Column("invoice_number", sa.String(50), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("amount_paid", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("amount_due", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("terms", sa.Text, nullable=True),
        sa.Column("external_provider", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "invoice_number", name="uq_invoices_tenant_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_invoices_tenant_idempotency_key"),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"])
    op.create_index("ix_invoices_job_id", "invoices", ["job_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_due_date", "invoices", ["due_date"])
    op.create_index("ix_invoices_idempotency_key", "invoices", ["idempotency_key"])

    op.create_table(
        "invoice_line_items",
        *_timestamp_cols(),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("job_material_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("job_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_invoice_line_items_tenant_id", "invoice_line_items", ["tenant_id"])
    op.create_index("ix_invoice_line_items_invoice_id", "invoice_line_items", ["invoice_id"])

    op.create_table(
        "payments",
        *_timestamp_cols(),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", "external_id", name="uq_payments_tenant_provider_external"),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_customer_id", "payments", ["customer_id"])
    op.create_index("ix_payments_status", "payments", ["status"])

    op.create_table(
        "payment_allocations",
        *_timestamp_cols(),
        sa.Column("payment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_payment_allocations_tenant_id", "payment_allocations", ["tenant_id"])
    op.create_index("ix_payment_allocations_payment_id", "payment_allocations", ["payment_id"])
    op.create_index("ix_payment_allocations_invoice_id", "payment_allocations", ["invoice_id"])

    op.create_table(
        "refunds",
        *_timestamp_cols(),
        sa.Column("payment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="REQUESTED"),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_refunds_tenant_id", "refunds", ["tenant_id"])
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"])

    op.create_table(
        "credit_notes",
        *_timestamp_cols(),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("credit_note_number", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "credit_note_number", name="uq_credit_notes_tenant_number"),
    )
    op.create_index("ix_credit_notes_tenant_id", "credit_notes", ["tenant_id"])
    op.create_index("ix_credit_notes_invoice_id", "credit_notes", ["invoice_id"])
    op.create_index("ix_credit_notes_customer_id", "credit_notes", ["customer_id"])

    op.create_table(
        "credit_note_line_items",
        *_timestamp_cols(),
        sa.Column("credit_note_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_credit_note_line_items_tenant_id", "credit_note_line_items", ["tenant_id"])
    op.create_index("ix_credit_note_line_items_credit_note_id", "credit_note_line_items", ["credit_note_id"])

    op.create_table(
        "writeoff_requests",
        *_timestamp_cols(),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="REQUESTED"),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_writeoff_requests_tenant_id", "writeoff_requests", ["tenant_id"])
    op.create_index("ix_writeoff_requests_invoice_id", "writeoff_requests", ["invoice_id"])

    op.create_table(
        "job_costs",
        *_timestamp_cols(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("vendor_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_job_costs_tenant_id", "job_costs", ["tenant_id"])
    op.create_index("ix_job_costs_job_id", "job_costs", ["job_id"])

    op.create_table(
        "vendors",
        *_timestamp_cols(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_vendors_tenant_id", "vendors", ["tenant_id"])

    op.create_table(
        "vendor_bills",
        *_timestamp_cols(),
        sa.Column("vendor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("external_reference", sa.String(255), nullable=True),
    )
    op.create_index("ix_vendor_bills_tenant_id", "vendor_bills", ["tenant_id"])
    op.create_index("ix_vendor_bills_vendor_id", "vendor_bills", ["vendor_id"])
    op.create_index("ix_vendor_bills_job_id", "vendor_bills", ["job_id"])
    op.create_index("ix_vendor_bills_due_date", "vendor_bills", ["due_date"])

    op.create_table(
        "payouts",
        *_timestamp_cols(),
        sa.Column("vendor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="REQUESTED"),
        sa.Column("provider", sa.String(50), nullable=False, server_default="internal_test_payout"),
        sa.Column("external_reference", sa.String(255), nullable=True),
    )
    op.create_index("ix_payouts_tenant_id", "payouts", ["tenant_id"])
    op.create_index("ix_payouts_vendor_id", "payouts", ["vendor_id"])

    op.create_table(
        "cash_forecasts",
        *_timestamp_cols(),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("starting_cash", sa.Numeric(14, 2), nullable=True),
        sa.Column("starting_cash_source", sa.String(30), nullable=False, server_default="NOT_CONNECTED"),
    )
    op.create_index("ix_cash_forecasts_tenant_id", "cash_forecasts", ["tenant_id"])

    op.create_table(
        "cash_forecast_items",
        *_timestamp_cols(),
        sa.Column("forecast_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("type", sa.String(10), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PROJECTED"),
    )
    op.create_index("ix_cash_forecast_items_tenant_id", "cash_forecast_items", ["tenant_id"])
    op.create_index("ix_cash_forecast_items_forecast_id", "cash_forecast_items", ["forecast_id"])
    op.create_index("ix_cash_forecast_items_week_start", "cash_forecast_items", ["week_start"])

    op.create_table(
        "collection_actions",
        *_timestamp_cols(),
        sa.Column("invoice_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(30), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "tenant_id", "invoice_id", "action_type", name="uq_collection_actions_tenant_invoice_type"
        ),
    )
    op.create_index("ix_collection_actions_tenant_id", "collection_actions", ["tenant_id"])
    op.create_index("ix_collection_actions_invoice_id", "collection_actions", ["invoice_id"])
    op.create_index("ix_collection_actions_scheduled_for", "collection_actions", ["scheduled_for"])


def downgrade() -> None:
    op.drop_table("collection_actions")
    op.drop_table("cash_forecast_items")
    op.drop_table("cash_forecasts")
    op.drop_table("payouts")
    op.drop_table("vendor_bills")
    op.drop_table("vendors")
    op.drop_table("job_costs")
    op.drop_table("writeoff_requests")
    op.drop_table("credit_note_line_items")
    op.drop_table("credit_notes")
    op.drop_table("refunds")
    op.drop_table("payment_allocations")
    op.drop_table("payments")
    op.drop_table("invoice_line_items")
    op.drop_table("invoices")
    op.drop_column("organizations", "manual_starting_cash")
