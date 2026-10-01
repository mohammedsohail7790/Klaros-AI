"""Phase 17B-4: idempotent provisioning of the narrow, cross-tenant
SYSTEM DISCOVERY database role (`klaros_discovery`).

Design decision and full reasoning:
PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md §11 — Option A (a dedicated,
narrowly-scoped PostgreSQL role using column-level `GRANT SELECT` plus a
role-scoped RLS policy) over Option B (a SECURITY DEFINER function).

Five real code paths need to read across every tenant to find scheduling/
recovery candidates (see the log's §10 field inventory): Automation
discovery, Agent discovery, Morning Brief org discovery, Agent Recovery
discovery, EventBus stuck-event discovery. This script provisions the ONE
shared role those paths will eventually all use.

Round 4 proved the mechanism end-to-end on a single path first: `events`,
for `EventBus.reconcile_stuck_events()` (`backend/app/events/bus.py`),
chosen because it is the one path whose CURRENT code over-selects
relative to what it actually uses (today: `select(Event)`, the full ORM
row including `payload`; actually used downstream: `id`, `tenant_id`,
`event_type` only — see the log's §10 code trace) — narrowing it is a
genuine, real tightening, not just a mechanism proof.

Round 5 extended `_TABLE_COLUMN_GRANTS` to the other 4 discovery paths'
tables: `automations`/`automation_versions` (Automation discovery),
`agents`/`agent_versions` (Agent discovery), `organizations` (Morning
Brief discovery), `agent_executions` (Agent Recovery discovery — already
narrow, `id` only, in real application code today). Grants now cover all
5 real discovery paths at the database level. The live application code
at all 5 call sites (`automation_service.py`, `agent_trigger_service.py`,
`morning_brief_service.py`, `agent_recovery_service.py`, `bus.py`) is
STILL not rewired to actually open a `klaros_discovery`-bound session —
that remains deliberately deferred (new engine/session plumbing, a
distinct, higher-risk change — see the log's §34 item 2).

Round 15 (§37d/§39) added a 6th grant, `mcp_client_credentials`, for a
structurally different real cross-tenant read: MCP credential
authentication's token-hash-to-tenant resolution step
(`McpCredentialService.authenticate`,
`backend/app/services/mcp_service.py`) — the first of the 6 paths this
script's grants are actually wired into live application code for (via
the new `discovery_engine`/`discovery_session_maker` in
`app/db/session.py`), proving the mechanism end-to-end in production
shape, not just at the database level like the other 5 still are.

This role is fundamentally different from `klaros_app`
(`provision_app_role.py`):
  - `klaros_app` gets full table-level SELECT/INSERT/UPDATE/DELETE on
    every table (subject to each table's RLS policies) — it is the
    ordinary per-tenant runtime identity.
  - `klaros_discovery` gets ZERO INSERT/UPDATE/DELETE grants anywhere,
    ever, on any table — it exists to find cross-tenant CANDIDATES, never
    to mutate them (every real mutation path re-establishes a genuine
    per-tenant session with `set_tenant_context()` before writing
    anything, unchanged by this script). It also gets SELECT on only the
    specific COLUMNS listed in `_TABLE_COLUMN_GRANTS`, via PostgreSQL's
    native column-level privilege grammar (`GRANT SELECT (col, col) ON
    table TO role`) — not whole-table SELECT — so a query attempting to
    read an ungranted column (e.g. `events.payload`) fails with a
    `permission denied for column` error at the database level,
    independent of what the application's own query code asks for.
  - No `ALTER DEFAULT PRIVILEGES` grant for future tables — unlike
    `klaros_app`, this role's access must stay an explicit, reviewable
    allow-list; a table added by a future migration is NOT automatically
    readable by `klaros_discovery`.

Role attributes mirror `klaros_app`'s restricted shape exactly: LOGIN,
NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOBYPASSRLS, NOREPLICATION, never a
table owner — the brief's explicit requirement that the discovery
identity be "incapable of arbitrary tenant data access."

Usage:
    python -m scripts.db.provision_discovery_role

Required environment variables:
    DATABASE_MIGRATION_URL (or DATABASE_URL as a fallback) — an
        asyncpg-style SQLAlchemy URL for the schema-owning role (same
        contract as provision_app_role.py).
    DISCOVERY_DB_USER — the restricted role's name (e.g.
        `klaros_discovery`).
    DISCOVERY_DB_PASSWORD — the restricted role's password. Never logged,
        never printed, never written to any file by this script.

Safe to run multiple times (CREATE ROLE is guarded by an existence check;
every GRANT statement is naturally idempotent in PostgreSQL).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import asyncpg

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Table -> exact column allow-list. Adding a table here (a future round,
# for Automation/Agent/Morning-Brief/Agent-Recovery discovery) is the only
# change needed to extend this role's reach — deliberately explicit, never
# a wildcard.
#
# Round 5 update: extended to the other 4 discovery paths' tables, using
# the exact field inventories captured in
# PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md §10/§11 — matched to actual
# downstream field use (see §11's reasoning for why the Automation/Agent
# grants are wider than the brief's bare illustrative id/status list: the
# discovered rows' `condition`/`steps`/`triggers`/`instructions_snapshot`
# are consumed directly by dispatch without a second per-tenant re-fetch).
_TABLE_COLUMN_GRANTS: dict[str, tuple[str, ...]] = {
    # EventBus.reconcile_stuck_events (backend/app/events/bus.py) — real
    # current usage is id/tenant_id/event_type (re-enqueue + logging);
    # status and created_at are also granted since they are the query's
    # own WHERE-clause filter columns (status = PUBLISHED, created_at <
    # cutoff) — a column used only to filter, never returned, still needs
    # SELECT privilege on it under Postgres's column-privilege model.
    # `payload` and every other column are deliberately NOT granted.
    "events": ("id", "tenant_id", "event_type", "status", "created_at"),
    # AutomationService.check_and_dispatch_scheduled (backend/app/services/
    # automation_service.py) — discovery query joins Automation +
    # AutomationVersion, filtering on Automation.status/AutomationVersion.
    # trigger_type, then dispatch consumes AutomationVersion.condition/
    # steps directly without a second per-tenant fetch (§11's reasoning).
    "automations": ("id", "tenant_id", "status", "published_version_id"),
    "automation_versions": ("id", "trigger_type", "trigger_config", "condition", "steps"),
    # AgentTriggerService.check_and_dispatch_scheduled (backend/app/
    # services/agent_trigger_service.py) — mirrors the Automation shape:
    # discovery filters on Agent.status/current_version_id, dispatch
    # consumes AgentVersion.triggers/instructions_snapshot directly.
    "agents": ("id", "tenant_id", "status", "current_version_id"),
    "agent_versions": ("id", "triggers", "instructions_snapshot"),
    # MorningBriefService.check_and_generate_scheduled (backend/app/
    # services/morning_brief_service.py) — reads only the
    # morning-brief-scheduling columns of `organizations`, never any other
    # tenant data. `organizations` has no tenant_id column of its own (it
    # IS the tenant root — id is the tenant identity), so this grant does
    # not need/get an RLS carve-out the way the other tables below do.
    "organizations": (
        "id",
        "timezone",
        "morning_brief_enabled",
        "morning_brief_local_time",
        "morning_brief_timezone",
    ),
    # AgentRecoveryService._find_stale_candidates (backend/app/services/
    # agent_recovery_service.py) — already the narrowest of the 5 paths in
    # real application code today: only `id` is ever selected.
    "agent_executions": ("id",),
    # Round 15 (§37d/§39): a 6th, structurally different legitimate
    # cross-tenant read — MCP credential authentication
    # (McpCredentialService.authenticate,
    # backend/app/services/mcp_service.py). Unlike the 5 paths above (which
    # find CANDIDATES to later dispatch into a per-tenant session), this
    # one resolves WHICH TENANT a caller-presented secret belongs to in the
    # first place — the same structural role `organizations.slug` plays
    # for human login (§37c), except `mcp_client_credentials` genuinely has
    # no non-tenant-scoped root table to bounce off, so a narrow discovery
    # grant is the only safe way to do this lookup at all under real RLS.
    # `token_hash` is the query's own WHERE-clause filter (never returned
    # to any caller — only used to find the matching row), `status` lets
    # the resolution step early-reject a REVOKED credential without a
    # second round-trip, `id`/`tenant_id` are the two columns the caller
    # actually needs (to then open a genuine per-tenant `klaros_app`
    # session and re-fetch/update the full row for real, with real RLS —
    # see `McpCredentialService.authenticate`'s two-step shape). Every
    # other column (`name`, `role`, `created_by`, `revoked_at`,
    # `last_used_at`, ...) is deliberately NOT granted — this role never
    # needs them and must never be able to read them.
    "mcp_client_credentials": ("id", "tenant_id", "token_hash", "status"),
}


def _sqlalchemy_url_to_asyncpg_dsn(url: str) -> str:
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _quote_ident(name: str) -> str:
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(
            f"refusing to use {name!r} as a SQL identifier — must match "
            f"{_VALID_IDENTIFIER.pattern} (this is a defensive guard, not a "
            "real-world naming constraint: role/table/column names here are "
            "operator- or code-controlled config, never end-user input)"
        )
    return f'"{name}"'


def _dollar_quote(value: str) -> str:
    import secrets

    for _ in range(1000):
        tag = f"pw_{secrets.token_hex(16)}"
        delimiter = f"${tag}$"
        if delimiter not in value:
            return f"{delimiter}{value}{delimiter}"
    raise RuntimeError("could not generate a non-colliding dollar-quote tag")


async def provision_discovery_role(
    *,
    owner_dsn: str,
    discovery_user: str,
    discovery_password: str,
) -> None:
    role_ident = _quote_ident(discovery_user)
    conn = await asyncpg.connect(owner_dsn)
    try:
        database_name = await conn.fetchval("SELECT current_database()")
        db_ident = _quote_ident(database_name)

        role_exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", discovery_user)
        quoted_password = _dollar_quote(discovery_password)
        if role_exists:
            await conn.execute(
                f"ALTER ROLE {role_ident} WITH LOGIN PASSWORD {quoted_password} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"
            )
        else:
            await conn.execute(
                f"CREATE ROLE {role_ident} WITH LOGIN PASSWORD {quoted_password} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"
            )

        await conn.execute(f"GRANT CONNECT ON DATABASE {db_ident} TO {role_ident}")
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role_ident}")

        for table, columns in _TABLE_COLUMN_GRANTS.items():
            table_ident = _quote_ident(table)
            for col in columns:
                if not _VALID_IDENTIFIER.match(col):
                    raise ValueError(f"refusing to use {col!r} as a column identifier")
            col_list = ", ".join(_quote_ident(c) for c in columns)
            # Column-level SELECT only — no whole-table SELECT, no
            # INSERT/UPDATE/DELETE at all, on this or any other table.
            await conn.execute(f"GRANT SELECT ({col_list}) ON {table_ident} TO {role_ident}")

            # Round 5 addition: a table this role is granted column-level
            # SELECT on may ALSO have real per-tenant RLS enforcement (e.g.
            # `automations`/`agents`/`agent_versions`/`agent_executions`/
            # `automation_versions` — see PHASE_17B4_REAL_RLS_IMPLEMENTATION_
            # LOG.md §11's design). Without a role-scoped carve-out policy,
            # klaros_discovery's session (which never calls
            # set_tenant_context, so current_tenant_id() is NULL for it)
            # would be granted the COLUMNS but see ZERO ROWS under the
            # ordinary tenant_select policy — silently breaking cross-tenant
            # discovery rather than narrowing it. This permissive
            # `FOR SELECT TO klaros_discovery USING (true)` policy is
            # scoped to this one named role only (no other role, including
            # klaros_app or PUBLIC, is affected) and is harmless — a no-op —
            # on a table that has no RLS enabled at all (e.g. `organizations`,
            # `events` today), since a policy only has any effect once RLS is
            # switched on for that table. It is intentionally created here,
            # in this idempotent provisioning script, rather than inside an
            # Alembic migration: a migration referencing `TO klaros_discovery`
            # would fail outright if it runs before this role has ever been
            # provisioned (migrations and role provisioning have no
            # guaranteed ordering relative to each other, per
            # provision_app_role.py's own docstring for klaros_app), whereas
            # this script always runs after the role it creates exists, in
            # the same transaction-free, safe-to-rerun style as every GRANT
            # above it.
            await conn.execute(f"DROP POLICY IF EXISTS discovery_select ON {table_ident}")
            await conn.execute(
                f"CREATE POLICY discovery_select ON {table_ident} "
                f"FOR SELECT TO {role_ident} USING (true)"
            )

        # Deliberately NO `ALTER DEFAULT PRIVILEGES` — unlike klaros_app,
        # this role's reach must never auto-extend to new tables.
    finally:
        await conn.close()


async def _main() -> None:
    owner_url = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("DATABASE_URL")
    discovery_user = os.environ.get("DISCOVERY_DB_USER")
    discovery_password = os.environ.get("DISCOVERY_DB_PASSWORD")

    missing = [
        name
        for name, val in (
            ("DATABASE_MIGRATION_URL or DATABASE_URL", owner_url),
            ("DISCOVERY_DB_USER", discovery_user),
            ("DISCOVERY_DB_PASSWORD", discovery_password),
        )
        if not val
    ]
    if missing:
        print(f"provision_discovery_role: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    if owner_url and "postgresql" not in owner_url:
        print(
            "provision_discovery_role: DATABASE_MIGRATION_URL/DATABASE_URL is not a "
            f"PostgreSQL URL ({owner_url.split('://', 1)[0]}://...) — this script is "
            "PostgreSQL-only, skipping.",
            file=sys.stderr,
        )
        sys.exit(0)

    assert owner_url and discovery_user and discovery_password
    dsn = _sqlalchemy_url_to_asyncpg_dsn(owner_url)
    await provision_discovery_role(owner_dsn=dsn, discovery_user=discovery_user, discovery_password=discovery_password)
    print(f"provision_discovery_role: role {discovery_user!r} provisioned/updated successfully.")


if __name__ == "__main__":
    asyncio.run(_main())
