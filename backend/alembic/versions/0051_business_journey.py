"""Phase 13 (Business Orchestration Foundation): `business_journeys` — the
thin, tenant-owned coordinator table connecting Discovery -> Blueprint ->
Recommendations into one persisted, resumable journey. RLS audit-mode
instrumented, same treatment as every tenant-scoped table since 0040.

One-active-journey-per-tenant is enforced with a real partial unique
index (`status NOT IN ('COMPLETED', 'ABANDONED')`), never only an
application-level check — mirrors 0043's
`uq_business_blueprints_one_active_per_tenant` and 0050's
one-PUBLISHED-version-per-website pattern exactly.

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"
_TENANT_TABLES = ("business_journeys",)


def upgrade() -> None:
    op.create_table(
        "business_journeys",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="DISCOVERY_ACTIVE"),
        sa.Column(
            "discovery_session_id", sa.Uuid(as_uuid=True), sa.ForeignKey("discovery_sessions.id"), nullable=True
        ),
        sa.Column(
            "blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=True
        ),
        sa.Column(
            "recommendation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("recommendation_runs.id"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_business_journeys_tenant_id", "business_journeys", ["tenant_id"])
    op.create_index("ix_business_journeys_status", "business_journeys", ["status"])
    op.create_index(
        "ix_business_journeys_discovery_session_id", "business_journeys", ["discovery_session_id"]
    )
    op.create_index("ix_business_journeys_blueprint_id", "business_journeys", ["blueprint_id"])
    op.create_index(
        "ix_business_journeys_recommendation_run_id", "business_journeys", ["recommendation_run_id"]
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE UNIQUE INDEX uq_business_journeys_one_active_per_tenant
            ON business_journeys (tenant_id)
            WHERE status NOT IN ('COMPLETED', 'ABANDONED')
            """
        )
    else:
        # SQLite (test suite's Base.metadata.create_all() engine) supports
        # partial indexes with an identical WHERE clause via plain DDL —
        # mirrors 0043's business_blueprints partial-unique-index guard.
        op.execute(
            """
            CREATE UNIQUE INDEX uq_business_journeys_one_active_per_tenant
            ON business_journeys (tenant_id)
            WHERE status NOT IN ('COMPLETED', 'ABANDONED')
            """
        )

    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040/0041/0043/0044/0050's identical guard/rationale.
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

    op.drop_index("uq_business_journeys_one_active_per_tenant", table_name="business_journeys")
    op.drop_index("ix_business_journeys_recommendation_run_id", table_name="business_journeys")
    op.drop_index("ix_business_journeys_blueprint_id", table_name="business_journeys")
    op.drop_index("ix_business_journeys_discovery_session_id", table_name="business_journeys")
    op.drop_index("ix_business_journeys_status", table_name="business_journeys")
    op.drop_index("ix_business_journeys_tenant_id", table_name="business_journeys")
    op.drop_table("business_journeys")
