"""Phase 1.1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1): the
VerticalExtension / DomainDefinition registry.

Two tables:
  - `vertical_extensions`: tenant-independent reference data (no tenant_id,
    no RLS — same treatment as `integration_provider_catalog`, added in the
    next migration). Seeded with `medical_tourism` and `dropshipping` rows
    (status=BETA, since their own table families ship in later phases —
    KLAROS_FINAL_DOMAIN_MODEL.md's Medical Tourism/Dropshipping sections)
    so the registry mechanism itself is exercisable and testable now.
  - `organization_vertical_extensions`: tenant-scoped join table (an org
    opting into a vertical). Gets the same RLS AUDIT-MODE instrumentation
    Phase 0 applied to its first 5 tables (see
    alembic/versions/0040_rls_audit_mode_tier1.py) — `ENABLE ROW LEVEL
    SECURITY` (not `FORCE`) plus a permissive `USING (true)` policy. This is
    the concrete "RLS-on-day-one for every new table" requirement
    (KLAROS_FINAL_SECURITY_MODEL.md §D, KLAROS_FINAL_DATABASE_ARCHITECTURE.md)
    applied at the SAME maturity level the rest of the codebase's RLS
    rollout has actually reached — audit mode, not enforced — since jumping
    straight to `FORCE ROW LEVEL SECURITY` for one new table while the
    other ~190 tenant-scoped tables remain app-layer-only would be a
    false sense of security specific to this one table, not a real
    boundary; the enforcement flip is a separate, later, whole-of-system
    decision (0.3 in the Phase 0 plan), not a Phase 1 task.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"


def upgrade() -> None:
    op.create_table(
        "vertical_extensions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.String(50), nullable=False, server_default="1.0.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="BETA"),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("configuration_schema", sa.JSON(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.UniqueConstraint("key", name="uq_vertical_extensions_key"),
    )
    op.create_index("ix_vertical_extensions_key", "vertical_extensions", ["key"])
    op.create_index("ix_vertical_extensions_status", "vertical_extensions", ["status"])

    op.create_table(
        "organization_vertical_extensions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "vertical_extension_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("vertical_extensions.id"),
            nullable=False,
        ),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "vertical_extension_id", name="uq_org_vertical_extension_tenant_vertical"
        ),
    )
    op.create_index("ix_organization_vertical_extensions_tenant_id", "organization_vertical_extensions", ["tenant_id"])
    op.create_index(
        "ix_organization_vertical_extensions_vertical_extension_id",
        "organization_vertical_extensions",
        ["vertical_extension_id"],
    )

    # Seed the two verticals named explicitly in KLAROS_FINAL_DOMAIN_MODEL.md
    # / KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1. Fixed UUIDs so this
    # migration is idempotent-in-spirit (re-running upgrade/downgrade cycles
    # in tests always produces the same row identity) and so later
    # migrations/seed data can reference these ids stably if ever needed.
    vertical_extensions = sa.table(
        "vertical_extensions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("version", sa.String),
        sa.column("status", sa.String),
        sa.column("capabilities", sa.JSON),
        sa.column("configuration_schema", sa.JSON),
        sa.column("extra_metadata", sa.JSON),
    )
    from app.data.vertical_extension_seed import SEED_VERTICALS

    op.bulk_insert(
        vertical_extensions,
        [
            {
                "id": v["id"],
                "key": v["key"],
                "name": v["name"],
                "description": v["description"],
                "version": v["version"],
                "status": str(v["status"]),
                "capabilities": v["capabilities"],
                "configuration_schema": v["configuration_schema"],
                "extra_metadata": v["extra_metadata"],
            }
            for v in SEED_VERTICALS
        ],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040's identical guard/rationale.
        return

    op.execute("ALTER TABLE organization_vertical_extensions ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {POLICY_NAME} ON organization_vertical_extensions
        FOR ALL
        USING (true)
        WITH CHECK (true)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON organization_vertical_extensions")
        op.execute("ALTER TABLE organization_vertical_extensions DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        "ix_organization_vertical_extensions_vertical_extension_id",
        table_name="organization_vertical_extensions",
    )
    op.drop_index("ix_organization_vertical_extensions_tenant_id", table_name="organization_vertical_extensions")
    op.drop_table("organization_vertical_extensions")

    op.drop_index("ix_vertical_extensions_status", table_name="vertical_extensions")
    op.drop_index("ix_vertical_extensions_key", table_name="vertical_extensions")
    op.drop_table("vertical_extensions")
