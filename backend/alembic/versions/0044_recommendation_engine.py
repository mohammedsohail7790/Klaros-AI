"""Phase 3 (Recommendation Engine): `recommendation_runs` / `recommendations`
(tenant-owned, RLS audit-mode instrumented, same treatment as 0043's five
tables) plus one additive column on the existing, tenant-independent
`integration_provider_catalog` registry (`capabilities` — the smallest
possible extension needed for deterministic capability->provider
matching, never a new duplicate registry table; see
app/models/integration_catalog.py's module docstring and
app/data/integration_provider_catalog_seed.py for the backfilled tag data).

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"

_TENANT_TABLES = ("recommendation_runs", "recommendations")


def upgrade() -> None:
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=False
        ),
        sa.Column("blueprint_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMPLETED"),
        sa.Column("triggered_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("verticals_considered", sa.JSON(), nullable=False),
        sa.Column("recommendation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_recommendation_runs_tenant_id", "recommendation_runs", ["tenant_id"])
    op.create_index("ix_recommendation_runs_blueprint_id", "recommendation_runs", ["blueprint_id"])
    op.create_index(
        "ix_recommendation_runs_tenant_blueprint", "recommendation_runs", ["tenant_id", "blueprint_id"]
    )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("recommendation_runs.id"), nullable=False
        ),
        sa.Column(
            "blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=False
        ),
        sa.Column("blueprint_version", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("capability_key", sa.String(150), nullable=False),
        sa.Column("provider_key", sa.String(50), nullable=True),
        sa.Column("provider_implementation_status", sa.String(20), nullable=True),
        sa.Column("tool_name", sa.String(150), nullable=True),
        sa.Column("what", sa.Text(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("based_on", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("cost_estimate", sa.JSON(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("alternatives", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_vertical_key", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PROPOSED"),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_recommendations_confidence_range"),
    )
    op.create_index("ix_recommendations_tenant_id", "recommendations", ["tenant_id"])
    op.create_index("ix_recommendations_run_id", "recommendations", ["run_id"])
    op.create_index("ix_recommendations_blueprint_id", "recommendations", ["blueprint_id"])
    op.create_index("ix_recommendations_type", "recommendations", ["type"])
    op.create_index("ix_recommendations_capability_key", "recommendations", ["capability_key"])
    op.create_index("ix_recommendations_provider_key", "recommendations", ["provider_key"])
    op.create_index("ix_recommendations_tool_name", "recommendations", ["tool_name"])
    op.create_index("ix_recommendations_status", "recommendations", ["status"])
    op.create_index(
        "ix_recommendations_tenant_blueprint_status", "recommendations", ["tenant_id", "blueprint_id", "status"]
    )
    op.create_index("ix_recommendations_tenant_run", "recommendations", ["tenant_id", "run_id"])
    # Expression-based (COALESCE) unique index, not a plain multi-column
    # one — see app/models/recommendation.py's __table_args__ comment: a
    # plain unique index does not catch two CAPABILITY rows (provider_key/
    # tool_name both NULL) for the same run+capability, since NULL is
    # never equal to itself in SQL unique-index semantics.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE UNIQUE INDEX uq_recommendations_run_type_capability_provider_tool
            ON recommendations (
                run_id, type, capability_key,
                COALESCE(provider_key, ''), COALESCE(tool_name, '')
            )
            """
        )
    else:
        op.create_index(
            "uq_recommendations_run_type_capability_provider_tool",
            "recommendations",
            ["run_id", "type", "capability_key", "provider_key", "tool_name"],
            unique=True,
        )

    # Additive column on the existing Phase 1 registry (see module
    # docstring) — nullable=False with a default so the backfill below
    # never leaves a NULL row on an already-populated table.
    op.add_column(
        "integration_provider_catalog",
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"),
    )

    from app.data.integration_provider_catalog_seed import SEED_PROVIDERS

    catalog = sa.table(
        "integration_provider_catalog",
        sa.column("provider_key", sa.String),
        sa.column("capabilities", sa.JSON),
    )
    bind = op.get_bind()
    for p in SEED_PROVIDERS:
        caps = p.get("capabilities") or []
        if not caps:
            continue
        op.execute(catalog.update().where(catalog.c.provider_key == p["provider_key"]).values(capabilities=caps))

    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040/0041/0043's identical guard/rationale.
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

    op.drop_column("integration_provider_catalog", "capabilities")

    op.drop_index("uq_recommendations_run_type_capability_provider_tool", table_name="recommendations")
    op.drop_index("ix_recommendations_tenant_run", table_name="recommendations")
    op.drop_index("ix_recommendations_tenant_blueprint_status", table_name="recommendations")
    op.drop_index("ix_recommendations_status", table_name="recommendations")
    op.drop_index("ix_recommendations_tool_name", table_name="recommendations")
    op.drop_index("ix_recommendations_provider_key", table_name="recommendations")
    op.drop_index("ix_recommendations_capability_key", table_name="recommendations")
    op.drop_index("ix_recommendations_type", table_name="recommendations")
    op.drop_index("ix_recommendations_blueprint_id", table_name="recommendations")
    op.drop_index("ix_recommendations_run_id", table_name="recommendations")
    op.drop_index("ix_recommendations_tenant_id", table_name="recommendations")
    op.drop_table("recommendations")

    op.drop_index("ix_recommendation_runs_tenant_blueprint", table_name="recommendation_runs")
    op.drop_index("ix_recommendation_runs_blueprint_id", table_name="recommendation_runs")
    op.drop_index("ix_recommendation_runs_tenant_id", table_name="recommendation_runs")
    op.drop_table("recommendation_runs")
