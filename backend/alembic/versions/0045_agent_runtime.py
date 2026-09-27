"""Phase 4 (Agent Runtime foundation): `agents` / `agent_versions` /
`agent_tool_permissions` / `agent_executions` (tenant-owned, RLS
audit-mode instrumented, same treatment as 0043/0044's tenant-owned
tables) plus three additive, nullable columns on the existing
`approval_requests` table (`agent_id`/`agent_version_id`/
`agent_execution_id`) so an Agent-initiated approval can be reconstructed
and resumed through the unchanged approval boundary — see
app/models/approval.py's Phase 4 column comment and
app/services/approval_execution_service.py.

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"

_TENANT_TABLES = ("agents", "agent_versions", "agent_tool_permissions", "agent_executions")


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("autonomy_tier", sa.String(30), nullable=False, server_default="OBSERVE"),
        sa.Column("acting_role", sa.String(30), nullable=False),
        sa.Column("current_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "source_blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=True
        ),
        sa.Column("source_blueprint_version", sa.Integer(), nullable=True),
        sa.Column(
            "source_recommendation_id", sa.Uuid(as_uuid=True), sa.ForeignKey("recommendations.id"), nullable=True
        ),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])
    op.create_index("ix_agents_status", "agents", ["status"])
    op.create_index("ix_agents_tenant_status", "agents", ["tenant_id", "status"])

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("instructions_snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_permissions_snapshot", sa.JSON(), nullable=False),
        sa.Column("memory_refs", sa.JSON(), nullable=False),
        sa.Column("triggers", sa.JSON(), nullable=False),
        sa.Column("max_executions_per_hour", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_concurrent_executions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_tool_chain_depth", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approval_policy_override", sa.String(20), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("first_executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index("ix_agent_versions_tenant_id", "agent_versions", ["tenant_id"])
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_index("ix_agent_versions_status", "agent_versions", ["status"])
    op.create_index("ix_agent_versions_tenant_agent", "agent_versions", ["tenant_id", "agent_id"])

    # Circular FK (agents.current_version_id -> agent_versions.id,
    # agent_versions.agent_id -> agents.id): added after both tables exist,
    # matching app/models/agent.py's `use_alter=True` declaration. SQLite
    # cannot ALTER-add a constraint at all (no batch-mode used here, since
    # this is a live-migration file, not a from-scratch model build) — the
    # test suite's SQLite path builds tables straight from
    # `Base.metadata.create_all()` instead (see conftest.py), which SQLAlchemy
    # handles natively via `use_alter`, so skipping this ALTER on SQLite
    # only affects a real `alembic upgrade` run against SQLite (dev-only,
    # never production), never the ORM-level constraint itself.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_agents_current_version", "agents", "agent_versions", ["current_version_id"], ["id"]
        )

    op.create_table(
        "agent_tool_permissions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("constraint_config", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("agent_id", "tool_name", name="uq_agent_tool_permissions_agent_tool"),
    )
    op.create_index("ix_agent_tool_permissions_tenant_id", "agent_tool_permissions", ["tenant_id"])
    op.create_index("ix_agent_tool_permissions_agent_id", "agent_tool_permissions", ["agent_id"])
    op.create_index("ix_agent_tool_permissions_tool_name", "agent_tool_permissions", ["tool_name"])
    op.create_index(
        "ix_agent_tool_permissions_tenant_agent", "agent_tool_permissions", ["tenant_id", "agent_id"]
    )

    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agent_versions.id"), nullable=False),
        sa.Column("trigger_source", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("triggered_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("tool_input_summary", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column(
            "approval_request_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approval_requests.id"), nullable=True
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "agent_id", "idempotency_key", name="uq_agent_executions_tenant_agent_idempotency"
        ),
    )
    op.create_index("ix_agent_executions_tenant_id", "agent_executions", ["tenant_id"])
    op.create_index("ix_agent_executions_agent_id", "agent_executions", ["agent_id"])
    op.create_index("ix_agent_executions_agent_version_id", "agent_executions", ["agent_version_id"])
    op.create_index("ix_agent_executions_status", "agent_executions", ["status"])
    op.create_index("ix_agent_executions_tenant_agent", "agent_executions", ["tenant_id", "agent_id"])
    op.create_index(
        "ix_agent_executions_tenant_agent_version", "agent_executions", ["tenant_id", "agent_version_id"]
    )
    op.create_index("ix_agent_executions_tenant_status", "agent_executions", ["tenant_id", "status"])

    # Additive, nullable columns on the existing approval_requests table —
    # see module docstring.
    op.add_column("approval_requests", sa.Column("agent_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("approval_requests", sa.Column("agent_version_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("approval_requests", sa.Column("agent_execution_id", sa.Uuid(as_uuid=True), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040/0041/0043/0044's identical guard/rationale.
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

    op.drop_column("approval_requests", "agent_execution_id")
    op.drop_column("approval_requests", "agent_version_id")
    op.drop_column("approval_requests", "agent_id")

    op.drop_index("ix_agent_executions_tenant_status", table_name="agent_executions")
    op.drop_index("ix_agent_executions_tenant_agent_version", table_name="agent_executions")
    op.drop_index("ix_agent_executions_tenant_agent", table_name="agent_executions")
    op.drop_index("ix_agent_executions_status", table_name="agent_executions")
    op.drop_index("ix_agent_executions_agent_version_id", table_name="agent_executions")
    op.drop_index("ix_agent_executions_agent_id", table_name="agent_executions")
    op.drop_index("ix_agent_executions_tenant_id", table_name="agent_executions")
    op.drop_table("agent_executions")

    op.drop_index("ix_agent_tool_permissions_tenant_agent", table_name="agent_tool_permissions")
    op.drop_index("ix_agent_tool_permissions_tool_name", table_name="agent_tool_permissions")
    op.drop_index("ix_agent_tool_permissions_agent_id", table_name="agent_tool_permissions")
    op.drop_index("ix_agent_tool_permissions_tenant_id", table_name="agent_tool_permissions")
    op.drop_table("agent_tool_permissions")

    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_agents_current_version", "agents", type_="foreignkey")

    op.drop_index("ix_agent_versions_tenant_agent", table_name="agent_versions")
    op.drop_index("ix_agent_versions_status", table_name="agent_versions")
    op.drop_index("ix_agent_versions_agent_id", table_name="agent_versions")
    op.drop_index("ix_agent_versions_tenant_id", table_name="agent_versions")
    op.drop_table("agent_versions")

    op.drop_index("ix_agents_tenant_status", table_name="agents")
    op.drop_index("ix_agents_status", table_name="agents")
    op.drop_index("ix_agents_tenant_id", table_name="agents")
    op.drop_table("agents")
