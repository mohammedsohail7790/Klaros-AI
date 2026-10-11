"""Medical Tourism referral pipeline: provider verification, patient-approved provider referrals, handoff tasks, structured intake, commission states.

Additive only: nothing is dropped and no row is rewritten.
  * medical_tourism_providers            + verification_status (default UNVERIFIED), verification_updated_by/at. EXISTING providers become UNVERIFIED on purpose:
                                           none of them can be matched or referred to until a person verifies them against credential evidence.
  * medical_tourism_patient_leads        + preferred_language, preferred_contact_time, timeframe, procedure_text, destination_text (all nullable)
  * medical_tourism_referral_commissions + provider_referral_id, qualifying_outcome, invoiced_at, paid_at, status_note; referral_id becomes NULLABLE (a commission may
                                           belong to a provider referral instead of a retention-programme referral). Status is a string column: INVOICED/PAID/DISPUTED need no DDL.
  * new tables: medical_tourism_provider_referrals, medical_tourism_referral_approvals (append-only), medical_tourism_referral_deliveries (append-only),
                medical_tourism_handoff_tasks. Row-level security on PostgreSQL as in 0065; no DELETE policy on the append-only tables.

Revision ID: 0066_mt_referrals
Revises: 0065_halla_consent_evidence
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066_mt_referrals"
down_revision: Union[str, None] = "0065_halla_consent_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_NEW_TABLES = (
    ("medical_tourism_provider_referrals", ("select", "insert", "update")),
    ("medical_tourism_referral_approvals", ("select", "insert")),  # append-only
    ("medical_tourism_referral_deliveries", ("select", "insert", "update")),  # update: erasure removes the payload values only
    ("medical_tourism_handoff_tasks", ("select", "insert", "update")),
)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
    ]


def upgrade() -> None:
    op.add_column("medical_tourism_providers", sa.Column("verification_status", sa.String(16), nullable=False, server_default="UNVERIFIED"))
    op.add_column("medical_tourism_providers", sa.Column("verification_updated_by", _UUID, nullable=True))
    op.add_column("medical_tourism_providers", sa.Column("verification_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_medical_tourism_providers_verification_status", "medical_tourism_providers", ["verification_status"])

    for col, length in (("preferred_language", 35), ("preferred_contact_time", 80), ("timeframe", 80), ("procedure_text", 80), ("destination_text", 80)):
        op.add_column("medical_tourism_patient_leads", sa.Column(col, sa.String(length), nullable=True))

    with op.batch_alter_table("medical_tourism_referral_commissions") as batch:
        batch.alter_column("referral_id", existing_type=_UUID, nullable=True)
        batch.add_column(sa.Column("provider_referral_id", _UUID, nullable=True))
        batch.add_column(sa.Column("qualifying_outcome", sa.String(32), nullable=True))
        batch.add_column(sa.Column("invoiced_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("status_note", sa.String(500), nullable=True))
        batch.create_index("ix_mt_commissions_provider_referral_id", ["provider_referral_id"])

    op.create_table(
        "medical_tourism_provider_referrals", *_base(),
        sa.Column("lead_id", _UUID, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("provider_id", _UUID, sa.ForeignKey("medical_tourism_providers.id"), nullable=False),
        sa.Column("procedure_id", _UUID, sa.ForeignKey("medical_tourism_procedures.id"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("permitted_fields", sa.JSON(), nullable=False),
        sa.Column("approval_id", _UUID, nullable=True),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_block_reason", sa.String(64), nullable=True),
        sa.Column("clinic_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clinic_response_by", _UUID, nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("outcome_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_evidence", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "lead_id", "provider_id", name="uq_mt_provider_referrals_tenant_lead_provider"),
    )
    op.create_index("ix_mt_provider_referrals_tenant_id", "medical_tourism_provider_referrals", ["tenant_id"])
    op.create_index("ix_mt_provider_referrals_lead_id", "medical_tourism_provider_referrals", ["lead_id"])
    op.create_index("ix_mt_provider_referrals_provider_id", "medical_tourism_provider_referrals", ["provider_id"])
    op.create_index("ix_mt_provider_referrals_tenant_status", "medical_tourism_provider_referrals", ["tenant_id", "status"])

    op.create_table(
        "medical_tourism_referral_approvals", *_base(),
        sa.Column("lead_id", _UUID, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("provider_id", _UUID, sa.ForeignKey("medical_tourism_providers.id"), nullable=False),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("permitted_fields", sa.JSON(), nullable=False),
        sa.Column("patient_informed", sa.Boolean(), nullable=False),
        sa.Column("wording_version", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(24), nullable=True),
        sa.Column("recorded_by", _UUID, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_referral_approvals_tenant_id", "medical_tourism_referral_approvals", ["tenant_id"])
    op.create_index("ix_mt_referral_approvals_lead_id", "medical_tourism_referral_approvals", ["lead_id"])
    op.create_index("ix_mt_referral_approvals_lead_provider", "medical_tourism_referral_approvals", ["tenant_id", "lead_id", "provider_id", "recorded_at"])

    op.create_table(
        "medical_tourism_referral_deliveries", *_base(),
        sa.Column("referral_id", _UUID, sa.ForeignKey("medical_tourism_provider_referrals.id"), nullable=False),
        sa.Column("lead_id", _UUID, nullable=False),
        sa.Column("provider_id", _UUID, nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("transport", sa.String(24), nullable=False),
        sa.Column("fields_sent", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_mt_referral_deliveries_tenant_key"),
    )
    op.create_index("ix_mt_referral_deliveries_tenant_id", "medical_tourism_referral_deliveries", ["tenant_id"])
    op.create_index("ix_mt_referral_deliveries_referral_id", "medical_tourism_referral_deliveries", ["referral_id"])
    op.create_index("ix_mt_referral_deliveries_lead_id", "medical_tourism_referral_deliveries", ["lead_id"])

    op.create_table(
        "medical_tourism_handoff_tasks", *_base(),
        sa.Column("lead_id", _UUID, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=True),
        sa.Column("dedupe_key", sa.String(160), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_mt_handoff_tasks_tenant_dedupe"),
    )
    op.create_index("ix_mt_handoff_tasks_tenant_id", "medical_tourism_handoff_tasks", ["tenant_id"])
    op.create_index("ix_mt_handoff_tasks_lead_id", "medical_tourism_handoff_tasks", ["lead_id"])
    op.create_index("ix_mt_handoff_tasks_tenant_status", "medical_tourism_handoff_tasks", ["tenant_id", "status"])

    if op.get_bind().dialect.name != "postgresql":
        return
    for table, verbs in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for verb in verbs:
            if verb == "select":
                op.execute(f"CREATE POLICY tenant_select ON {table} FOR SELECT USING (tenant_id = current_tenant_id())")
            elif verb == "insert":
                op.execute(f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
            else:
                op.execute(f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, _verbs in _NEW_TABLES:
            for policy in ("tenant_select", "tenant_insert", "tenant_update"):
                op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    for table, _verbs in reversed(_NEW_TABLES):
        op.drop_table(table)
    with op.batch_alter_table("medical_tourism_referral_commissions") as batch:
        batch.drop_index("ix_mt_commissions_provider_referral_id")
        for col in ("status_note", "paid_at", "invoiced_at", "qualifying_outcome", "provider_referral_id"):
            batch.drop_column(col)
        batch.alter_column("referral_id", existing_type=_UUID, nullable=False)  # fails if provider-referral commissions exist: remove them first
    for col in ("destination_text", "procedure_text", "timeframe", "preferred_contact_time", "preferred_language"):
        op.drop_column("medical_tourism_patient_leads", col)
    op.drop_index("ix_medical_tourism_providers_verification_status", table_name="medical_tourism_providers")
    for col in ("verification_updated_at", "verification_updated_by", "verification_status"):
        op.drop_column("medical_tourism_providers", col)
