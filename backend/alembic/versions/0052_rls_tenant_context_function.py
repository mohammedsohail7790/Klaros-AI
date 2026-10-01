"""Phase 17B-4: `current_tenant_id()` — the single, reusable, fail-closed
tenant-context accessor every real (non-audit-mode) RLS policy from this
phase onward is built on.

Design proven against real PostgreSQL (restricted `klaros_app` role, not
the table-owner connection) in PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md
§17b before this migration was written — 43/43 checks passed on a 5-table
subset (customers, webhook_events, automations, users, invoices) using
raw SQL against a disposable instance. This migration is that same,
unchanged SQL, now committed as a real migration for the first time.

Why a shared SQL function instead of repeating the expression inline in
every one of the ~125 tenant-owned tables' policies: one place to read,
review, and (if ever needed) fix, instead of ~500 nearly-identical
`USING`/`WITH CHECK` clauses (4 policies x ~125 tables) each hand-copying
the same cast/NULLIF/exception logic — a single point of audit for the
one expression this whole phase's security property rests on.

NULL-safety contract (see log for the full real-Postgres proof):
`current_setting('app.tenant_id', true)` — the `true` "missing_ok" arg —
returns NULL, never raises, when the session-local `app.tenant_id` GUC was
never set (i.e. `set_tenant_context()` was never called on this session/
transaction). `NULLIF(..., '')` additionally collapses an empty string to
NULL (belt-and-braces against a caller that sets the GUC to `''` instead
of leaving it unset). Casting NULL to `::uuid` yields NULL, not an error.
A genuinely malformed non-UUID string (should never happen through the
real `set_tenant_context()` call path, which always passes a real
`uuid.UUID`, but a policy must not trust that blindly) is caught by the
`EXCEPTION WHEN invalid_text_representation` handler and mapped to NULL
too. In every one of those three cases (unset / empty / malformed), a
policy clause of the form `tenant_id = current_tenant_id()` evaluates to
SQL NULL under three-valued logic, which `USING`/`WITH CHECK` treats as
"exclude this row" — so no tenant context ever means zero tenant rows
visible or writable, never a raised exception a caller could mishandle,
and never (by construction — there is no branch that returns `true`) a
bypass.

`STABLE` (not `IMMUTABLE`): its result can change within a single SQL
statement's lifetime only if `app.tenant_id` itself changes mid-statement,
which cannot happen (`set_tenant_context()` always runs as a separate,
prior statement) — `STABLE` correctly documents "constant for the
duration of one query" to the planner without the stronger, incorrect
`IMMUTABLE` claim that its result depends on nothing session-local at all.

PostgreSQL-only, exactly like every RLS migration since 0040 — silently a
no-op on SQLite (this project's dev/test fallback engine), which has no
equivalent and no `CREATE FUNCTION ... LANGUAGE plpgsql` syntax.

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
        BEGIN
          RETURN NULLIF(current_setting('app.tenant_id', true), '')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Safe only once no policy still references it — 0053 (which depends on
    # this function) must be downgraded first; enforced naturally by
    # Alembic's linear revision order (0053's downgrade runs before this
    # one when downgrading past both).
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")
