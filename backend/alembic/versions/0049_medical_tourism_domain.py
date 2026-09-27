"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md, KLAROS_DOMAIN_
EXTENSIBILITY_SPEC.md §3): the Medical Tourism vertical's domain table
family -- seven new tenant-scoped tables (Provider, ProviderCredential,
Procedure, ProviderProcedure, PatientLead, Consultation,
ReferralCommission) plus two additive, nullable columns on the existing
`referral_rewards` table (currency, commission_basis), mirroring 0044's
precedent of one additive column on an existing table alongside new
tables. Also updates the Phase 1 `medical_tourism` VerticalExtension
registry row (seeded empty/BETA/"0.0.0-registry-only" in 0041) now that
this vertical's table family actually ships -- capabilities populated,
version bumped, status promoted to ACTIVE. RLS audit-mode instrumented on
every new tenant-scoped table, same treatment as every table since 0040.

Revision ID: 0049
Revises: 0048
Create Date: 2026-09-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"

_TENANT_TABLES = (
    "medical_tourism_providers",
    "medical_tourism_provider_credentials",
    "medical_tourism_procedures",
    "medical_tourism_provider_procedures",
    "medical_tourism_patient_leads",
    "medical_tourism_consultations",
    "medical_tourism_referral_commissions",
)


def upgrade() -> None:
    op.create_table(
        "medical_tourism_providers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("practitioner_name", sa.String(255), nullable=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_medical_tourism_providers_tenant_idempotency_key"
        ),
    )
    op.create_index("ix_medical_tourism_providers_tenant_id", "medical_tourism_providers", ["tenant_id"])
    op.create_index("ix_medical_tourism_providers_country", "medical_tourism_providers", ["country"])
    op.create_index("ix_medical_tourism_providers_status", "medical_tourism_providers", ["status"])
    op.create_index(
        "ix_medical_tourism_providers_idempotency_key", "medical_tourism_providers", ["idempotency_key"]
    )

    op.create_table(
        "medical_tourism_provider_credentials",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "provider_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_providers.id"), nullable=False
        ),
        sa.Column("credential_type", sa.String(120), nullable=False),
        sa.Column("issuing_authority", sa.String(255), nullable=True),
        sa.Column("credential_number", sa.String(120), nullable=True),
        sa.Column("issued_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("verified_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_medical_tourism_provider_credentials_tenant_id",
        "medical_tourism_provider_credentials",
        ["tenant_id"],
    )
    op.create_index(
        "ix_medical_tourism_provider_credentials_provider_id",
        "medical_tourism_provider_credentials",
        ["provider_id"],
    )
    op.create_index(
        "ix_medical_tourism_provider_credentials_status", "medical_tourism_provider_credentials", ["status"]
    )

    op.create_table(
        "medical_tourism_procedures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("typical_destination_countries", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_medical_tourism_procedures_tenant_idempotency_key"
        ),
    )
    op.create_index("ix_medical_tourism_procedures_tenant_id", "medical_tourism_procedures", ["tenant_id"])
    op.create_index("ix_medical_tourism_procedures_category", "medical_tourism_procedures", ["category"])
    op.create_index("ix_medical_tourism_procedures_status", "medical_tourism_procedures", ["status"])
    op.create_index(
        "ix_medical_tourism_procedures_idempotency_key", "medical_tourism_procedures", ["idempotency_key"]
    )

    op.create_table(
        "medical_tourism_provider_procedures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "provider_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_providers.id"), nullable=False
        ),
        sa.Column(
            "procedure_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_procedures.id"), nullable=False
        ),
        sa.Column("estimated_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint(
            "tenant_id", "provider_id", "procedure_id", name="uq_medical_tourism_provider_procedure_unique"
        ),
    )
    op.create_index(
        "ix_medical_tourism_provider_procedures_tenant_id", "medical_tourism_provider_procedures", ["tenant_id"]
    )
    op.create_index(
        "ix_medical_tourism_provider_procedures_provider_id",
        "medical_tourism_provider_procedures",
        ["provider_id"],
    )
    op.create_index(
        "ix_medical_tourism_provider_procedures_procedure_id",
        "medical_tourism_provider_procedures",
        ["procedure_id"],
    )
    op.create_index(
        "ix_medical_tourism_provider_procedures_status", "medical_tourism_provider_procedures", ["status"]
    )

    op.create_table(
        "medical_tourism_patient_leads",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.Uuid(as_uuid=True), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column(
            "procedure_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_procedures.id"), nullable=True
        ),
        sa.Column("preferred_destination_country", sa.String(2), nullable=True),
        sa.Column("medical_history_summary", sa.Text(), nullable=True),
        sa.Column("travel_start_date", sa.Date(), nullable=True),
        sa.Column("travel_end_date", sa.Date(), nullable=True),
        sa.Column("has_insurance", sa.Boolean(), nullable=True),
        sa.Column("insurance_notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "lead_id", name="uq_medical_tourism_patient_leads_tenant_lead"),
    )
    op.create_index(
        "ix_medical_tourism_patient_leads_tenant_id", "medical_tourism_patient_leads", ["tenant_id"]
    )
    op.create_index("ix_medical_tourism_patient_leads_lead_id", "medical_tourism_patient_leads", ["lead_id"])
    op.create_index(
        "ix_medical_tourism_patient_leads_procedure_id", "medical_tourism_patient_leads", ["procedure_id"]
    )

    op.create_table(
        "medical_tourism_consultations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("appointment_id", sa.Uuid(as_uuid=True), sa.ForeignKey("appointments.id"), nullable=False),
        sa.Column(
            "provider_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_providers.id"), nullable=False
        ),
        sa.Column(
            "procedure_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_procedures.id"), nullable=True
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "appointment_id", name="uq_medical_tourism_consultations_tenant_appointment"
        ),
    )
    op.create_index(
        "ix_medical_tourism_consultations_tenant_id", "medical_tourism_consultations", ["tenant_id"]
    )
    op.create_index(
        "ix_medical_tourism_consultations_appointment_id", "medical_tourism_consultations", ["appointment_id"]
    )
    op.create_index(
        "ix_medical_tourism_consultations_provider_id", "medical_tourism_consultations", ["provider_id"]
    )
    op.create_index("ix_medical_tourism_consultations_status", "medical_tourism_consultations", ["status"])

    op.create_table(
        "medical_tourism_referral_commissions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referral_id", sa.Uuid(as_uuid=True), sa.ForeignKey("referrals.id"), nullable=False),
        sa.Column(
            "provider_id", sa.Uuid(as_uuid=True), sa.ForeignKey("medical_tourism_providers.id"), nullable=True
        ),
        sa.Column("basis", sa.String(20), nullable=False, server_default="PERCENTAGE"),
        sa.Column("commission_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("flat_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("computed_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.UniqueConstraint(
            "tenant_id", "referral_id", name="uq_medical_tourism_referral_commissions_tenant_referral"
        ),
    )
    op.create_index(
        "ix_medical_tourism_referral_commissions_tenant_id",
        "medical_tourism_referral_commissions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_medical_tourism_referral_commissions_referral_id",
        "medical_tourism_referral_commissions",
        ["referral_id"],
    )
    op.create_index(
        "ix_medical_tourism_referral_commissions_provider_id",
        "medical_tourism_referral_commissions",
        ["provider_id"],
    )
    op.create_index(
        "ix_medical_tourism_referral_commissions_status", "medical_tourism_referral_commissions", ["status"]
    )

    # Additive, nullable columns on the existing retention ReferralReward
    # table (see app/models/retention.py's updated docstring) -- no
    # backfill needed since both are nullable and no existing row can have
    # a meaningful value for either.
    op.add_column("referral_rewards", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column("referral_rewards", sa.Column("commission_basis", sa.String(20), nullable=True))

    # Promote the Phase 1 medical_tourism VerticalExtension registry row
    # now that its table family actually ships. Single source of truth is
    # app/data/vertical_extension_seed.py -- imported here exactly like
    # 0041/0044 import their own seed/backfill data, so the migration and
    # the test suite can never drift.
    from app.data.vertical_extension_seed import MEDICAL_TOURISM_ID, SEED_VERTICALS

    updated = next(v for v in SEED_VERTICALS if v["id"] == MEDICAL_TOURISM_ID)
    vertical_extensions = sa.table(
        "vertical_extensions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("version", sa.String),
        sa.column("status", sa.String),
        sa.column("capabilities", sa.JSON),
        sa.column("description", sa.Text),
    )
    op.execute(
        vertical_extensions.update()
        .where(vertical_extensions.c.id == MEDICAL_TOURISM_ID)
        .values(
            version=updated["version"],
            status=str(updated["status"]),
            capabilities=updated["capabilities"],
            description=updated["description"],
        )
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only -- see 0040/0041/0044's identical guard/rationale.
        return

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {POLICY_NAME} ON {table}
            FOR ALL
            USING (true)
            WITH CHECK (true)
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Revert the vertical_extensions row to its pre-Phase-10 registry-only
    # state (mirrors 0041's original seed values exactly).
    from app.data.vertical_extension_seed import MEDICAL_TOURISM_ID

    vertical_extensions = sa.table(
        "vertical_extensions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("version", sa.String),
        sa.column("status", sa.String),
        sa.column("capabilities", sa.JSON),
        sa.column("description", sa.Text),
    )
    op.execute(
        vertical_extensions.update()
        .where(vertical_extensions.c.id == MEDICAL_TOURISM_ID)
        .values(
            version="0.0.0-registry-only",
            status="BETA",
            capabilities=[],
            description=(
                "Cross-border patient leads, provider/procedure directory, and referral "
                "commissions. Table family ships in a later phase; this row exists so the "
                "registry mechanism can be exercised now."
            ),
        )
    )

    op.drop_column("referral_rewards", "commission_basis")
    op.drop_column("referral_rewards", "currency")

    op.drop_index(
        "ix_medical_tourism_referral_commissions_status", table_name="medical_tourism_referral_commissions"
    )
    op.drop_index(
        "ix_medical_tourism_referral_commissions_provider_id",
        table_name="medical_tourism_referral_commissions",
    )
    op.drop_index(
        "ix_medical_tourism_referral_commissions_referral_id",
        table_name="medical_tourism_referral_commissions",
    )
    op.drop_index(
        "ix_medical_tourism_referral_commissions_tenant_id", table_name="medical_tourism_referral_commissions"
    )
    op.drop_table("medical_tourism_referral_commissions")

    op.drop_index("ix_medical_tourism_consultations_status", table_name="medical_tourism_consultations")
    op.drop_index(
        "ix_medical_tourism_consultations_provider_id", table_name="medical_tourism_consultations"
    )
    op.drop_index(
        "ix_medical_tourism_consultations_appointment_id", table_name="medical_tourism_consultations"
    )
    op.drop_index("ix_medical_tourism_consultations_tenant_id", table_name="medical_tourism_consultations")
    op.drop_table("medical_tourism_consultations")

    op.drop_index(
        "ix_medical_tourism_patient_leads_procedure_id", table_name="medical_tourism_patient_leads"
    )
    op.drop_index("ix_medical_tourism_patient_leads_lead_id", table_name="medical_tourism_patient_leads")
    op.drop_index("ix_medical_tourism_patient_leads_tenant_id", table_name="medical_tourism_patient_leads")
    op.drop_table("medical_tourism_patient_leads")

    op.drop_index(
        "ix_medical_tourism_provider_procedures_status", table_name="medical_tourism_provider_procedures"
    )
    op.drop_index(
        "ix_medical_tourism_provider_procedures_procedure_id",
        table_name="medical_tourism_provider_procedures",
    )
    op.drop_index(
        "ix_medical_tourism_provider_procedures_provider_id",
        table_name="medical_tourism_provider_procedures",
    )
    op.drop_index(
        "ix_medical_tourism_provider_procedures_tenant_id", table_name="medical_tourism_provider_procedures"
    )
    op.drop_table("medical_tourism_provider_procedures")

    op.drop_index(
        "ix_medical_tourism_procedures_idempotency_key", table_name="medical_tourism_procedures"
    )
    op.drop_index("ix_medical_tourism_procedures_status", table_name="medical_tourism_procedures")
    op.drop_index("ix_medical_tourism_procedures_category", table_name="medical_tourism_procedures")
    op.drop_index("ix_medical_tourism_procedures_tenant_id", table_name="medical_tourism_procedures")
    op.drop_table("medical_tourism_procedures")

    op.drop_index(
        "ix_medical_tourism_provider_credentials_status", table_name="medical_tourism_provider_credentials"
    )
    op.drop_index(
        "ix_medical_tourism_provider_credentials_provider_id",
        table_name="medical_tourism_provider_credentials",
    )
    op.drop_index(
        "ix_medical_tourism_provider_credentials_tenant_id",
        table_name="medical_tourism_provider_credentials",
    )
    op.drop_table("medical_tourism_provider_credentials")

    op.drop_index(
        "ix_medical_tourism_providers_idempotency_key", table_name="medical_tourism_providers"
    )
    op.drop_index("ix_medical_tourism_providers_status", table_name="medical_tourism_providers")
    op.drop_index("ix_medical_tourism_providers_country", table_name="medical_tourism_providers")
    op.drop_index("ix_medical_tourism_providers_tenant_id", table_name="medical_tourism_providers")
    op.drop_table("medical_tourism_providers")
