"""App Builder: projects, immutable versions with their files, durable runs and chat messages.

Additive only: five new tables, nothing existing is altered or dropped. Row-level security on PostgreSQL as in 0066
(tenant_id = current_tenant_id()). `builder_versions` and `builder_files` also get a DELETE policy because old versions are
pruned (the published version is never pruned); `builder_projects` gets one so an owner can delete a project.

Rollback: `alembic downgrade 0070_consent_wordings` drops the five tables and their policies. That deletes generated
projects; export them first if they matter (GET /builder/projects/{id}/export).

Revision ID: 0071_app_builder
Revises: 0070_consent_wordings
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071_app_builder"
down_revision: Union[str, None] = "0070_consent_wordings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)
_JSON = sa.JSON()
_TABLES = (
    ("builder_projects", ("select", "insert", "update", "delete")),
    ("builder_versions", ("select", "insert", "delete")),
    ("builder_files", ("select", "insert", "delete")),
    ("builder_runs", ("select", "insert", "update")),
    ("builder_messages", ("select", "insert")),
)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "builder_projects", *_base(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="new"),
        sa.Column("spec", _JSON, nullable=True),
        sa.Column("initial_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("current_version_id", _UUID, nullable=True),
        sa.Column("published_version_id", _UUID, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_paths", _JSON, nullable=False, server_default="[]"),
        sa.Column("created_by", _UUID, nullable=True),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_builder_projects_tenant_slug"),
    )
    op.create_index("ix_builder_projects_tenant_id", "builder_projects", ["tenant_id"])

    op.create_table(
        "builder_versions", *_base(),
        sa.Column("project_id", _UUID, sa.ForeignKey("builder_projects.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec", _JSON, nullable=False),
        sa.Column("manifest", _JSON, nullable=False, server_default="[]"),
        sa.Column("validation", _JSON, nullable=False, server_default="{}"),
        sa.Column("changes", _JSON, nullable=False, server_default="{}"),
        sa.Column("created_by", _UUID, nullable=True),
        sa.UniqueConstraint("project_id", "number", name="uq_builder_versions_project_number"),
    )
    op.create_index("ix_builder_versions_tenant_id", "builder_versions", ["tenant_id"])
    op.create_index("ix_builder_versions_project_id", "builder_versions", ["project_id"])

    op.create_table(
        "builder_files", *_base(),
        sa.Column("version_id", _UUID, sa.ForeignKey("builder_versions.id"), nullable=False),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version_id", "path", name="uq_builder_files_version_path"),
    )
    op.create_index("ix_builder_files_tenant_id", "builder_files", ["tenant_id"])
    op.create_index("ix_builder_files_version_id", "builder_files", ["version_id"])
    op.create_index("ix_builder_files_project_id", "builder_files", ["project_id"])

    op.create_table(
        "builder_runs", *_base(),
        sa.Column("project_id", _UUID, sa.ForeignKey("builder_projects.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("progress", _JSON, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("validation", _JSON, nullable=True),
        sa.Column("usage", _JSON, nullable=False, server_default="{}"),
        sa.Column("repair_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_version_id", _UUID, nullable=True),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_builder_runs_tenant_id", "builder_runs", ["tenant_id"])
    op.create_index("ix_builder_runs_project_id", "builder_runs", ["project_id"])
    op.create_index("ix_builder_runs_tenant_state", "builder_runs", ["tenant_id", "state"])

    op.create_table(
        "builder_messages", *_base(),
        sa.Column("project_id", _UUID, sa.ForeignKey("builder_projects.id"), nullable=False),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_builder_messages_tenant_id", "builder_messages", ["tenant_id"])
    op.create_index("ix_builder_messages_project_id", "builder_messages", ["project_id"])

    if op.get_bind().dialect.name != "postgresql":
        return
    for table, verbs in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for verb in verbs:
            if verb == "select":
                op.execute(f"CREATE POLICY tenant_select ON {table} FOR SELECT USING (tenant_id = current_tenant_id())")
            elif verb == "insert":
                op.execute(f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK (tenant_id = current_tenant_id())")
            elif verb == "update":
                op.execute(f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())")
            else:
                op.execute(f"CREATE POLICY tenant_delete ON {table} FOR DELETE USING (tenant_id = current_tenant_id())")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, _verbs in _TABLES:
            for policy in ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete"):
                op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    for table, _verbs in reversed(_TABLES):
        op.drop_table(table)
