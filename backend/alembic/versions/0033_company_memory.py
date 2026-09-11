"""Phase 13: Company Memory — the governed, tenant-scoped, auditable
persistence layer for durable owner preferences/business rules/context.
See app/models/company_memory.py.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_memories",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("memory_type", sa.String(length=30), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=2000), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("source_entity_type", sa.String(length=50), nullable=True),
        sa.Column("source_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_company_memories_tenant_id", "company_memories", ["tenant_id"])
    op.create_index("ix_company_memories_memory_type", "company_memories", ["memory_type"])
    op.create_index("ix_company_memories_key", "company_memories", ["key"])
    op.create_index("ix_company_memories_status", "company_memories", ["status"])
    # The hot query is get_context()'s "ACTIVE rows for this tenant" scan
    # and create_memory()/confirm_memory()'s "current ACTIVE row for this
    # (tenant, key)" lookup — a composite index serves both.
    op.create_index(
        "ix_company_memories_tenant_key_status", "company_memories", ["tenant_id", "key", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_company_memories_tenant_key_status", table_name="company_memories")
    op.drop_index("ix_company_memories_status", table_name="company_memories")
    op.drop_index("ix_company_memories_key", table_name="company_memories")
    op.drop_index("ix_company_memories_memory_type", table_name="company_memories")
    op.drop_index("ix_company_memories_tenant_id", table_name="company_memories")
    op.drop_table("company_memories")
