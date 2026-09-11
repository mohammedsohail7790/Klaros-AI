"""The generic Automation Engine: Automation / AutomationVersion /
AutomationExecution / AutomationExecutionStep. See app/models/automation.py.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("published_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_automations_tenant_id", "automations", ["tenant_id"])

    op.create_table(
        "automation_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("automation_id", sa.Uuid(as_uuid=True), sa.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("trigger_config", sa.JSON(), nullable=False),
        sa.Column("condition", sa.JSON(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("automation_id", "version_number", name="uq_automation_version_number"),
    )
    op.create_index("ix_automation_versions_tenant_id", "automation_versions", ["tenant_id"])
    op.create_index("ix_automation_versions_automation_id", "automation_versions", ["automation_id"])

    op.create_table(
        "automation_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("automation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_version_id", sa.Uuid(as_uuid=True), sa.ForeignKey("automation_versions.id"), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("source_event_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("current_step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("temporal_workflow_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "automation_version_id", "source_event_id", name="uq_automation_execution_version_event"
        ),
    )
    op.create_index("ix_automation_executions_tenant_id", "automation_executions", ["tenant_id"])
    op.create_index("ix_automation_executions_automation_id", "automation_executions", ["automation_id"])
    op.create_index("ix_automation_executions_automation_version_id", "automation_executions", ["automation_version_id"])

    op.create_table(
        "automation_execution_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=True), sa.ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_automation_execution_steps_tenant_id", "automation_execution_steps", ["tenant_id"])
    op.create_index("ix_automation_execution_steps_execution_id", "automation_execution_steps", ["execution_id"])


def downgrade() -> None:
    op.drop_index("ix_automation_execution_steps_execution_id", table_name="automation_execution_steps")
    op.drop_index("ix_automation_execution_steps_tenant_id", table_name="automation_execution_steps")
    op.drop_table("automation_execution_steps")

    op.drop_index("ix_automation_executions_automation_version_id", table_name="automation_executions")
    op.drop_index("ix_automation_executions_automation_id", table_name="automation_executions")
    op.drop_index("ix_automation_executions_tenant_id", table_name="automation_executions")
    op.drop_table("automation_executions")

    op.drop_index("ix_automation_versions_automation_id", table_name="automation_versions")
    op.drop_index("ix_automation_versions_tenant_id", table_name="automation_versions")
    op.drop_table("automation_versions")

    op.drop_index("ix_automations_tenant_id", table_name="automations")
    op.drop_table("automations")
