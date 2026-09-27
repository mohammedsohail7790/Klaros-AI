"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md): the Website Builder
foundation's relational schema — four new tenant-scoped tables (Website,
WebsiteVersion, WebsitePage, WebsiteSection). RLS audit-mode instrumented
on every new table, same treatment as every table since 0040. `websites.
current_published_version_id` is a nullable, deferred (use_alter) FK to
website_versions to avoid a circular create-table dependency (Website ->
WebsiteVersion -> Website), the same shape Postgres/Alembic requires for
any two tables that reference each other.

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "tenant_isolation_audit_policy"

_TENANT_TABLES = (
    "websites",
    "website_versions",
    "website_pages",
    "website_sections",
)


def upgrade() -> None:
    op.create_table(
        "websites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("blueprint_id", sa.Uuid(as_uuid=True), sa.ForeignKey("business_blueprints.id"), nullable=True),
        sa.Column("current_published_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_websites_tenant_id", "websites", ["tenant_id"])
    op.create_index("uq_websites_one_per_tenant", "websites", ["tenant_id"], unique=True)
    op.create_index("ix_websites_blueprint_id", "websites", ["blueprint_id"])

    op.create_table(
        "website_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("website_id", sa.Uuid(as_uuid=True), sa.ForeignKey("websites.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("theme", sa.JSON(), nullable=False),
        sa.Column("navigation", sa.JSON(), nullable=False),
        sa.Column("seo_defaults", sa.JSON(), nullable=False),
        sa.Column("generation_provenance", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("website_id", "version", name="uq_website_versions_website_version"),
    )
    op.create_index("ix_website_versions_tenant_id", "website_versions", ["tenant_id"])
    op.create_index("ix_website_versions_website_id", "website_versions", ["website_id"])
    op.create_index("ix_website_versions_status", "website_versions", ["status"])

    # Deferred FK from websites -> website_versions (see module docstring).
    # SQLite's Alembic dialect has no support for ALTER TABLE ADD
    # CONSTRAINT outside "batch mode" (a copy-and-move rewrite) -- skip it
    # there, matching this migration's existing RLS/partial-index
    # Postgres-only guards. SQLite does not enforce FK constraints by
    # default in this codebase's test engine anyway (no
    # `PRAGMA foreign_keys=ON` is set), so the ORM-level relationship is
    # unaffected; only the DB-level CHECK is skipped on that dialect.
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_websites_current_published",
            "websites",
            "website_versions",
            ["current_published_version_id"],
            ["id"],
        )

    op.create_table(
        "website_pages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "website_version_id", sa.Uuid(as_uuid=True), sa.ForeignKey("website_versions.id"), nullable=False
        ),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("seo", sa.JSON(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("website_version_id", "slug", name="uq_website_pages_version_slug"),
    )
    op.create_index("ix_website_pages_tenant_id", "website_pages", ["tenant_id"])
    op.create_index("ix_website_pages_website_version_id", "website_pages", ["website_version_id"])

    op.create_table(
        "website_sections",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("page_id", sa.Uuid(as_uuid=True), sa.ForeignKey("website_pages.id"), nullable=False),
        sa.Column("component_type", sa.String(40), nullable=False),
        sa.Column("props", sa.JSON(), nullable=False),
        sa.Column("data_source", sa.JSON(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_website_sections_tenant_id", "website_sections", ["tenant_id"])
    op.create_index("ix_website_sections_page_id", "website_sections", ["page_id"])
    op.create_index("ix_website_sections_component_type", "website_sections", ["component_type"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # At most one PUBLISHED version per website (mirrors
        # BusinessBlueprint's "one ACTIVE per tenant" partial index —
        # see app/models/website.py::WebsiteVersion.__table_args__).
        op.execute(
            """
            CREATE UNIQUE INDEX uq_website_versions_one_published_per_website
            ON website_versions (website_id)
            WHERE status = 'PUBLISHED'
            """
        )
    else:
        op.create_index(
            "uq_website_versions_one_published_per_website",
            "website_versions",
            ["website_id"],
            unique=True,
            sqlite_where=sa.text("status = 'PUBLISHED'"),
        )

    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-only -- see 0040/0041/0044/0049's identical guard/rationale.
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
        op.execute("DROP INDEX IF EXISTS uq_website_versions_one_published_per_website")
    else:
        op.drop_index("uq_website_versions_one_published_per_website", table_name="website_versions")

    op.drop_table("website_sections")
    op.drop_table("website_pages")
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_websites_current_published", "websites", type_="foreignkey")
    op.drop_table("website_versions")
    op.drop_table("websites")
