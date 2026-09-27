"""Phase 2 (KLAROS_BUSINESS_DISCOVERY_SPEC.md, KLAROS_BUSINESS_BLUEPRINT_SPEC.md,
KLAROS_ARCHITECTURE_RECONCILIATION.md #4): Business Discovery + Business
Blueprint tables.

Five new tables, all genuinely tenant-owned business data (no global
reference-data table this time, unlike vertical_extensions/
integration_provider_catalog in the prior two migrations) — every one gets
the same RLS AUDIT-MODE instrumentation Phase 0/Phase 1 applied
(`ENABLE ROW LEVEL SECURITY` + permissive `USING (true)` policy, never
`FORCE` — that flip is still a separate, later, whole-system decision):

  - `discovery_sessions` / `discovery_turns`: Business Discovery's own
    transient conversation state (KLAROS_BUSINESS_DISCOVERY_SPEC.md).
  - `business_blueprints` / `blueprint_sections` / `blueprint_claims`: the
    durable, versioned canonical business representation
    (KLAROS_BUSINESS_BLUEPRINT_SPEC.md).

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# GIN indexing requires jsonb (plain json has no default GIN operator
# class) — use jsonb on PostgreSQL, generic JSON elsewhere (SQLite test
# suite), matching how the ORM model declares this column.
_SECTION_DATA_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"

_TENANT_TABLES = (
    "discovery_sessions",
    "discovery_turns",
    "business_blueprints",
    "blueprint_sections",
    "blueprint_claims",
)


def upgrade() -> None:
    op.create_table(
        "business_blueprints",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "vertical_extension_id", sa.Uuid(as_uuid=True), sa.ForeignKey("vertical_extensions.id"), nullable=True
        ),
        sa.Column("supersedes_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "version", name="uq_business_blueprints_tenant_version"),
    )
    op.create_index("ix_business_blueprints_tenant_id", "business_blueprints", ["tenant_id"])
    op.create_index("ix_business_blueprints_status", "business_blueprints", ["status"])
    op.create_index(
        "uq_business_blueprints_one_active_per_tenant",
        "business_blueprints",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "blueprint_sections",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=False
        ),
        sa.Column("section_key", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="EMPTY"),
        sa.Column("data", _SECTION_DATA_TYPE, nullable=False),
        sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("blueprint_id", "section_key", name="uq_blueprint_sections_blueprint_key"),
    )
    op.create_index("ix_blueprint_sections_tenant_id", "blueprint_sections", ["tenant_id"])
    op.create_index("ix_blueprint_sections_blueprint_id", "blueprint_sections", ["blueprint_id"])
    op.create_index("ix_blueprint_sections_section_key", "blueprint_sections", ["section_key"])
    op.create_index("ix_blueprint_sections_status", "blueprint_sections", ["status"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "ix_blueprint_sections_data_gin", "blueprint_sections", ["data"], postgresql_using="gin"
        )

    op.create_table(
        "discovery_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=True
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("business_idea", sa.Text(), nullable=False),
        sa.Column("questions_asked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_questions", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_discovery_sessions_tenant_id", "discovery_sessions", ["tenant_id"])
    op.create_index("ix_discovery_sessions_blueprint_id", "discovery_sessions", ["blueprint_id"])
    op.create_index("ix_discovery_sessions_status", "discovery_sessions", ["status"])

    op.create_table(
        "discovery_turns",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "discovery_session_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("discovery_sessions.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="QUESTION_ANSWER"),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("extraction_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_discovery_turns_tenant_id", "discovery_turns", ["tenant_id"])
    op.create_index("ix_discovery_turns_discovery_session_id", "discovery_turns", ["discovery_session_id"])

    op.create_table(
        "blueprint_claims",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=False
        ),
        sa.Column("section_key", sa.String(40), nullable=False),
        sa.Column("claim_type", sa.String(20), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("provenance", sa.String(20), nullable=False),
        sa.Column(
            "discovery_turn_id", sa.Uuid(as_uuid=True), sa.ForeignKey("discovery_turns.id"), nullable=True
        ),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PROPOSED"),
        sa.Column("confirmed_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_blueprint_claims_tenant_id", "blueprint_claims", ["tenant_id"])
    op.create_index("ix_blueprint_claims_blueprint_id", "blueprint_claims", ["blueprint_id"])
    op.create_index("ix_blueprint_claims_section_key", "blueprint_claims", ["section_key"])
    op.create_index("ix_blueprint_claims_claim_type", "blueprint_claims", ["claim_type"])
    op.create_index("ix_blueprint_claims_status", "blueprint_claims", ["status"])
    op.create_index("ix_blueprint_claims_discovery_turn_id", "blueprint_claims", ["discovery_turn_id"])
    op.create_index(
        "ix_blueprint_claims_tenant_blueprint_status", "blueprint_claims", ["tenant_id", "blueprint_id", "status"]
    )
    op.create_index("ix_blueprint_claims_tenant_claim_type", "blueprint_claims", ["tenant_id", "claim_type"])

    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040/0041's identical guard/rationale.
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

    op.drop_index("ix_blueprint_claims_tenant_claim_type", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_tenant_blueprint_status", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_discovery_turn_id", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_status", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_claim_type", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_section_key", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_blueprint_id", table_name="blueprint_claims")
    op.drop_index("ix_blueprint_claims_tenant_id", table_name="blueprint_claims")
    op.drop_table("blueprint_claims")

    op.drop_index("ix_discovery_turns_discovery_session_id", table_name="discovery_turns")
    op.drop_index("ix_discovery_turns_tenant_id", table_name="discovery_turns")
    op.drop_table("discovery_turns")

    op.drop_index("ix_discovery_sessions_status", table_name="discovery_sessions")
    op.drop_index("ix_discovery_sessions_blueprint_id", table_name="discovery_sessions")
    op.drop_index("ix_discovery_sessions_tenant_id", table_name="discovery_sessions")
    op.drop_table("discovery_sessions")

    if bind.dialect.name == "postgresql":
        op.drop_index("ix_blueprint_sections_data_gin", table_name="blueprint_sections")
    op.drop_index("ix_blueprint_sections_status", table_name="blueprint_sections")
    op.drop_index("ix_blueprint_sections_section_key", table_name="blueprint_sections")
    op.drop_index("ix_blueprint_sections_blueprint_id", table_name="blueprint_sections")
    op.drop_index("ix_blueprint_sections_tenant_id", table_name="blueprint_sections")
    op.drop_table("blueprint_sections")

    op.drop_index("uq_business_blueprints_one_active_per_tenant", table_name="business_blueprints")
    op.drop_index("ix_business_blueprints_status", table_name="business_blueprints")
    op.drop_index("ix_business_blueprints_tenant_id", table_name="business_blueprints")
    op.drop_table("business_blueprints")
