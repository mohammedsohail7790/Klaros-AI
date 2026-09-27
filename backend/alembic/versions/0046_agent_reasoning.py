"""Phase 5 (Agent Runtime — bounded LLM-driven reasoning loop):
`agent_execution_steps` (tenant-owned, RLS audit-mode instrumented, same
treatment as 0040-0045's tenant-owned tables) plus additive columns on the
existing `agent_executions` table (`mode`, `goal`, `final_response`,
`termination_reason`, `reasoning_state`, `step_count`) so a multi-step
REASONING-mode execution can be distinguished from Phase 4's
SINGLE_ACTION executor without changing that executor's behavior or
schema meaning at all. `agent_executions.tool_name` is loosened to
nullable — a REASONING execution has no single caller-declared tool until
its first step runs (see app/models/agent.py's Phase 5 column comments and
app/services/agent_reasoning_service.py).

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"
_TENANT_TABLES = ("agent_execution_steps",)


def upgrade() -> None:
    bind = op.get_bind()

    # --- agent_executions: additive Phase 5 columns -----------------
    op.add_column(
        "agent_executions",
        sa.Column("mode", sa.String(20), nullable=False, server_default="SINGLE_ACTION"),
    )
    op.add_column("agent_executions", sa.Column("goal", sa.Text(), nullable=True))
    op.add_column("agent_executions", sa.Column("final_response", sa.Text(), nullable=True))
    op.add_column("agent_executions", sa.Column("termination_reason", sa.String(40), nullable=True))
    op.add_column(
        "agent_executions",
        sa.Column("reasoning_state", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "agent_executions",
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # tool_name: NOT NULL -> nullable. SQLite can't ALTER a column's
    # nullability directly without batch mode; the test suite's SQLite path
    # builds tables straight from Base.metadata.create_all() (see
    # conftest.py), so this ALTER only needs to run for real (Postgres)
    # migrations — matching the existing 0045 guard pattern for the
    # SQLite-incompatible circular FK.
    if bind.dialect.name == "postgresql":
        op.alter_column("agent_executions", "tool_name", existing_type=sa.String(255), nullable=True)

    # --- agent_execution_steps ---------------------------------------
    op.create_table(
        "agent_execution_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agent_executions.id"), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=True),
        sa.Column("input_summary", sa.JSON(), nullable=True),
        sa.Column("output_summary", sa.JSON(), nullable=True),
        sa.Column("decision_summary", sa.String(1000), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("execution_id", "step_number", name="uq_agent_execution_steps_execution_step"),
    )
    op.create_index("ix_agent_execution_steps_tenant_id", "agent_execution_steps", ["tenant_id"])
    op.create_index("ix_agent_execution_steps_execution_id", "agent_execution_steps", ["execution_id"])
    op.create_index("ix_agent_execution_steps_status", "agent_execution_steps", ["status"])
    op.create_index(
        "ix_agent_execution_steps_tenant_execution", "agent_execution_steps", ["tenant_id", "execution_id"]
    )

    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only — see 0040/.../0045's identical guard/rationale.
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

    op.drop_index("ix_agent_execution_steps_tenant_execution", table_name="agent_execution_steps")
    op.drop_index("ix_agent_execution_steps_status", table_name="agent_execution_steps")
    op.drop_index("ix_agent_execution_steps_execution_id", table_name="agent_execution_steps")
    op.drop_index("ix_agent_execution_steps_tenant_id", table_name="agent_execution_steps")
    op.drop_table("agent_execution_steps")

    if bind.dialect.name == "postgresql":
        op.alter_column("agent_executions", "tool_name", existing_type=sa.String(255), nullable=False)

    op.drop_column("agent_executions", "step_count")
    op.drop_column("agent_executions", "reasoning_state")
    op.drop_column("agent_executions", "termination_reason")
    op.drop_column("agent_executions", "final_response")
    op.drop_column("agent_executions", "goal")
    op.drop_column("agent_executions", "mode")
