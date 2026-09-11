"""Phase 13 follow-up: a real database-level constraint — at most one
ACTIVE row per (tenant_id, key) — enforced by a partial unique index, not
just application-level Python logic (Rule 30: "do not rely only on Python
locks"). Two genuinely concurrent create_memory/confirm_memory calls for
the same key can both read the same prior-ACTIVE row before either
commits; without this constraint both could commit a new ACTIVE row for
the same key. With it, the second write's INSERT/UPDATE raises a real
IntegrityError — CompanyMemoryService translates that into
MemoryConcurrentUpdateError (see app/services/company_memory_service.py)
rather than silently allowing two active values to coexist.

Syntax is identical on PostgreSQL and SQLite (both support partial
indexes with a WHERE clause), so this is a plain, portable op.execute —
no dialect branching needed.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_company_memories_one_active_per_key
        ON company_memories (tenant_id, key)
        WHERE status = 'ACTIVE'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_company_memories_one_active_per_key")
