"""Phase 17B-1: idempotent provisioning of the restricted runtime
application database role.

Klaros's existing deployment pattern (dev, CI, and — per
DEPLOYMENT_RUNBOOK.md — production today) connects to Postgres as the
cluster's own bootstrap/initdb role (`POSTGRES_USER`, e.g. `klaros`),
which PostgreSQL always creates as a SUPERUSER. Superusers own every
table and unconditionally bypass Row-Level Security, so the audit-mode
RLS instrumentation added in prior phases currently provides zero actual
protection (see PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md §7).

This script creates (or updates, idempotently) a SEPARATE, restricted
runtime role — LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOBYPASSRLS,
NOREPLICATION, and never a table owner — with exactly the DML privileges
the application needs (SELECT/INSERT/UPDATE/DELETE on tables, USAGE on
the schema, EXECUTE on functions such as the `vector` extension's
operators, and future-proofed via `ALTER DEFAULT PRIVILEGES` so tables
created by later Alembic migrations — still run as the owner role — are
automatically covered without a re-grant step).

It does NOT create or reassign table ownership: the connecting "owner"
role (whatever DATABASE_MIGRATION_URL/DATABASE_URL's role already is —
`klaros` in every environment today) keeps owning every table and keeps
running migrations exactly as before. This script only ever GRANTs to
the new role; it never REVOKEs from or alters the owner role.

Usage:
    python -m scripts.db.provision_app_role

Required environment variables:
    DATABASE_MIGRATION_URL (or DATABASE_URL as a fallback) — an
        asyncpg-style SQLAlchemy URL for a role that OWNS the schema
        (i.e. today's existing bootstrap/superuser role). This script
        connects with THIS role to run the GRANT/ALTER DEFAULT
        PRIVILEGES statements — it never authenticates as the app role
        it is creating.
    APP_DB_USER — the restricted role's name (e.g. `klaros_app`).
    APP_DB_PASSWORD — the restricted role's password. Never logged,
        never printed, never written to any file by this script.

Safe to run multiple times (CREATE ROLE is guarded by an existence
check; every GRANT/ALTER DEFAULT PRIVILEGES statement is naturally
idempotent in PostgreSQL). Safe to run before OR after
`alembic upgrade head` — if run before, `ALTER DEFAULT PRIVILEGES`
ensures tables the migrations go on to create are already covered; if
run after, the explicit `GRANT ... ON ALL TABLES/SEQUENCES` statements
cover what already exists, and `ALTER DEFAULT PRIVILEGES` still covers
anything created afterward (e.g. a later `alembic upgrade head`).
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sys

import asyncpg

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dollar_quote(value: str) -> str:
    """PostgreSQL's CREATE/ALTER ROLE ... PASSWORD clause requires a
    string literal token in the grammar (utility commands like this are
    not eligible for asyncpg's normal $1-style bind-parameter
    substitution), so the password has to be interpolated into the SQL
    text. Never done via naive quote-escaping — dollar-quoting with a
    randomly generated, collision-checked tag avoids every single-quote/
    backslash escaping pitfall entirely, which matters here because the
    value is a secret whose exact contents this script must not assume
    anything about."""
    for _ in range(1000):
        tag = f"pw_{secrets.token_hex(16)}"
        delimiter = f"${tag}$"
        if delimiter not in value:
            return f"{delimiter}{value}{delimiter}"
    raise RuntimeError("could not generate a non-colliding dollar-quote tag")


def _sqlalchemy_url_to_asyncpg_dsn(url: str) -> str:
    """asyncpg.connect() doesn't understand SQLAlchemy's
    `postgresql+asyncpg://` scheme or `+psycopg` etc. — normalize to the
    plain `postgresql://` scheme asyncpg expects."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _quote_ident(name: str) -> str:
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(
            f"refusing to use {name!r} as a SQL identifier — must match "
            f"{_VALID_IDENTIFIER.pattern} (this is a defensive guard, not a "
            "real-world naming constraint: role names are operator-controlled "
            "config, never end-user input)"
        )
    return f'"{name}"'


async def provision_app_role(
    *,
    owner_dsn: str,
    app_user: str,
    app_password: str,
    database_name: str | None = None,
) -> None:
    app_ident = _quote_ident(app_user)
    conn = await asyncpg.connect(owner_dsn)
    try:
        if database_name is None:
            database_name = await conn.fetchval("SELECT current_database()")
        db_ident = _quote_ident(database_name)

        role_exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", app_user
        )
        quoted_password = _dollar_quote(app_password)
        if role_exists:
            # Idempotent update path: keep the password and privilege flags
            # authoritative even if the role already existed (e.g. created
            # by an older version of this script, or manually).
            await conn.execute(
                f"ALTER ROLE {app_ident} WITH LOGIN PASSWORD {quoted_password} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"
            )
        else:
            await conn.execute(
                f"CREATE ROLE {app_ident} WITH LOGIN PASSWORD {quoted_password} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"
            )

        owner_role = await conn.fetchval("SELECT current_user")
        owner_ident = _quote_ident(owner_role)

        statements = [
            f"GRANT CONNECT ON DATABASE {db_ident} TO {app_ident}",
            f"GRANT USAGE ON SCHEMA public TO {app_ident}",
            # Existing objects (safe/no-op if the schema is still empty —
            # this script is designed to also run before any migration
            # has ever applied).
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_ident}",
            f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {app_ident}",
            f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {app_ident}",
            # Future objects: every Alembic migration continues to run as
            # the owner role, so anything a future migration creates is
            # automatically covered without re-running this script.
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_ident} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_ident}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_ident} IN SCHEMA public "
            f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {app_ident}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_ident} IN SCHEMA public "
            f"GRANT EXECUTE ON FUNCTIONS TO {app_ident}",
        ]
        for stmt in statements:
            await conn.execute(stmt)
    finally:
        await conn.close()


async def _main() -> None:
    owner_url = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("DATABASE_URL")
    app_user = os.environ.get("APP_DB_USER")
    app_password = os.environ.get("APP_DB_PASSWORD")

    missing = [
        name
        for name, val in (
            ("DATABASE_MIGRATION_URL or DATABASE_URL", owner_url),
            ("APP_DB_USER", app_user),
            ("APP_DB_PASSWORD", app_password),
        )
        if not val
    ]
    if missing:
        print(f"provision_app_role: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    if owner_url and "postgresql" not in owner_url:
        print(
            "provision_app_role: DATABASE_MIGRATION_URL/DATABASE_URL is not a PostgreSQL URL "
            f"({owner_url.split('://', 1)[0]}://...) — this script is PostgreSQL-only, skipping.",
            file=sys.stderr,
        )
        sys.exit(0)

    assert owner_url and app_user and app_password
    dsn = _sqlalchemy_url_to_asyncpg_dsn(owner_url)
    await provision_app_role(owner_dsn=dsn, app_user=app_user, app_password=app_password)
    print(f"provision_app_role: role {app_user!r} provisioned/updated successfully.")


if __name__ == "__main__":
    asyncio.run(_main())
