"""Phase 9 (MCP server exposure — KLAROS_FINAL_AGENT_MODEL.md's explicit
MCP-server carve-out; the MCP-client direction remains rejected per
ADR-002 and is not implemented anywhere in this change):

Two new tenant-owned tables, RLS audit-mode instrumented on day one, same
treatment as every other tenant table since Phase 0 (0040/0041/0043/0044/
0045):

  - `mcp_tool_exposures` — the explicit, admin-curated allowlist of which
    already-governed `ToolRegistry` tools an external MCP client may
    invoke, per tenant. No tool is ever exposed automatically.
  - `mcp_client_credentials` — scoped bearer credentials for external MCP
    clients, one per tenant + role, hashed (never stored in plaintext,
    never reversible — see app/models/mcp_server.py's module docstring for
    why this is a one-way hash rather than the existing Fernet-encrypted
    `IntegrationConnection` pattern).

Revision ID: 0048
Revises: 0047
Create Date: 2026-09-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"

_TENANT_TABLES = ("mcp_tool_exposures", "mcp_client_credentials")


def upgrade() -> None:
    op.create_table(
        "mcp_tool_exposures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "tool_name", name="uq_mcp_tool_exposures_tenant_tool"),
    )
    op.create_index("ix_mcp_tool_exposures_tenant_id", "mcp_tool_exposures", ["tenant_id"])
    op.create_index("ix_mcp_tool_exposures_tenant_enabled", "mcp_tool_exposures", ["tenant_id", "enabled"])

    op.create_table(
        "mcp_client_credentials",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_mcp_client_credentials_token_hash"),
    )
    op.create_index("ix_mcp_client_credentials_tenant_id", "mcp_client_credentials", ["tenant_id"])
    # No separate index on token_hash: the UniqueConstraint above already
    # creates one implicitly (Postgres backs every unique constraint with
    # an index) — an explicit second index would be a pure duplicate.
    op.create_index("ix_mcp_client_credentials_tenant_status", "mcp_client_credentials", ["tenant_id", "status"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040/0041/0043/0044/0045's identical guard/rationale.
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

    op.drop_index("ix_mcp_client_credentials_tenant_status", table_name="mcp_client_credentials")
    op.drop_index("ix_mcp_client_credentials_tenant_id", table_name="mcp_client_credentials")
    op.drop_table("mcp_client_credentials")

    op.drop_index("ix_mcp_tool_exposures_tenant_enabled", table_name="mcp_tool_exposures")
    op.drop_index("ix_mcp_tool_exposures_tenant_id", table_name="mcp_tool_exposures")
    op.drop_table("mcp_tool_exposures")
