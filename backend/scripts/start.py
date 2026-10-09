"""Klaros container entrypoint for the Halla pilot (Render "Docker Command": `python -m scripts.start`).

Replaces the inline shell one-liner (alembic -> seed -> connect -> uvicorn) with reviewed, tested, idempotent code. On every start it:

  1. PREFLIGHT   refuses to continue on an unsafe or incomplete configuration (names only are ever printed, never values)
  2. DATABASE    refuses a database that is not a Klaros database (an existing Prisma database, or any non-empty database Alembic
                 has never managed) BEFORE touching it
  3. MIGRATE     `alembic upgrade head` (a no-op when already at head)
  4. TENANTS     creates the pilot organizations that do not exist yet (fixed ids, so the webhook URLs never change)
  5. CONNECT     (re-)saves each tenant's Halla connection from the environment — the new secret replaces the old one, so rotating a
                 secret is "change the env var, redeploy". A failed health check never blocks boot: the credential is stored and signed
                 webhooks are accepted (see UNVERIFIED / ERROR in docs/KLAROS_HALLA_PRODUCTION_RUNBOOK.md)
  6. SERVE       exec's uvicorn on $PORT

Configuration (NON-secret) — HALLA_PILOT_TENANTS, a JSON list, e.g.

    [{"slug": "pilot-a", "name": "Pilot A", "klaros_tenant_id": "<uuid>", "halla_tenant_id": "<halla id>",
      "api_key_env": "HALLA_API_KEY_PILOT_A", "secret_env": "HALLA_WEBHOOK_SECRET_PILOT_A",
      "safety_profile": "medical_tourism",
      "owner_email": "owner@example.com", "owner_password_env": "OWNER_PASSWORD_PILOT_A"}]

The secrets themselves live ONLY in the environment variables named by `api_key_env` / `secret_env` / `owner_password_env`. Nothing is read
from arguments, nothing secret is printed or logged, and ids are masked in output.

Flags: --check (read-only verification, changes nothing), --no-serve (stop after setup), --skip-migrate, --skip-verify (save connections without calling Halla).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

INSECURE_JWT = "change-me-in-production"
REACHABLE = ("production", "staging")
_SLUG_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def mask(value: object, keep: int = 4) -> str:
    """`abcd****` — enough to recognise an id in a log, never enough to use it."""
    text = str(value or "")
    if len(text) <= keep:
        return "****"
    return text[:keep] + "****"


@dataclass(frozen=True)
class PilotTenant:
    slug: str
    name: str
    klaros_tenant_id: uuid.UUID
    halla_tenant_id: str
    api_key_env: str
    secret_env: str
    owner_email: str | None = None
    owner_password_env: str | None = None
    trial_days: int = 14
    safety_profile: str = ""


class ConfigError(Exception):
    pass


def parse_tenants(environ: Mapping[str, str]) -> list[PilotTenant]:
    raw = (environ.get("HALLA_PILOT_TENANTS") or "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError as exc:
        raise ConfigError("HALLA_PILOT_TENANTS is not valid JSON") from exc
    if not isinstance(items, list) or not items:
        raise ConfigError("HALLA_PILOT_TENANTS must be a non-empty JSON list")
    out: list[PilotTenant] = []
    seen_ids: set[uuid.UUID] = set()
    seen_slugs: set[str] = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] must be an object")
        for secretish in ("api_key", "signing_secret", "secret", "password", "owner_password"):
            if secretish in it:
                raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] must not contain a secret ('{secretish}'); name an environment variable instead")
        try:
            tid = uuid.UUID(str(it["klaros_tenant_id"]))
            slug = str(it["slug"]).strip().lower()
            name = str(it["name"]).strip()
            halla = str(it["halla_tenant_id"]).strip()
            key_env, secret_env = str(it["api_key_env"]).strip(), str(it["secret_env"]).strip()
        except KeyError as exc:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] is missing '{exc.args[0]}'") from exc
        except ValueError as exc:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] has an invalid klaros_tenant_id") from exc
        if not slug or set(slug) - _SLUG_OK or len(slug) > 100:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] has an invalid slug (lowercase letters, digits, hyphens)")
        if not name or len(name) > 255 or not halla or len(halla) > 100:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] has an invalid name or halla_tenant_id")
        if tid in seen_ids or slug in seen_slugs:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}] repeats another tenant's id or slug")
        seen_ids.add(tid)
        seen_slugs.add(slug)
        owner_email = (str(it["owner_email"]).strip().lower() if it.get("owner_email") else None)
        owner_pw_env = (str(it["owner_password_env"]).strip() if it.get("owner_password_env") else None)
        if bool(owner_email) != bool(owner_pw_env):
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}]: owner_email and owner_password_env go together")
        trial = it.get("trial_days", 14)
        if not isinstance(trial, int) or isinstance(trial, bool) or not 1 <= trial <= 365:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}]: trial_days must be 1-365")
        from app.services.pilot_safety import PROFILES

        profile = str(it.get("safety_profile") or "").strip()
        if profile not in PROFILES:
            raise ConfigError(f"HALLA_PILOT_TENANTS[{i}]: safety_profile must be one of {', '.join(sorted(PROFILES))}")
        out.append(PilotTenant(slug, name, tid, halla, key_env, secret_env, owner_email, owner_pw_env, trial, profile))
    return out


def url_identity(url: str) -> tuple[str, str]:
    """(database name, host) of a connection URL — never the user or password."""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    return (u.database or ""), (u.host or "")


def forbidden_hosts(environ: Mapping[str, str]) -> set[str]:
    return {h.strip().lower() for h in (environ.get("KLAROS_FORBIDDEN_DB_HOSTS") or "").split(",") if h.strip()}


def forbidden_names(environ: Mapping[str, str]) -> set[str]:
    return {h.strip().lower() for h in (environ.get("KLAROS_FORBIDDEN_DB_NAMES") or "").split(",") if h.strip()}


def name_forbidden(url: str, environ: Mapping[str, str]) -> bool:
    name, host = url_identity(url)
    return name.lower() in forbidden_names(environ) or (bool(host) and host.lower() in forbidden_hosts(environ))


def preflight(environ: Mapping[str, str], tenants: list[PilotTenant]) -> list[str]:
    """Problems that must stop the boot. Variable NAMES only — a value is never included."""
    env = (environ.get("ENV") or "development").lower()
    problems: list[str] = []
    if not environ.get("DATABASE_URL"):
        problems.append("DATABASE_URL is not set")
    elif not environ["DATABASE_URL"].startswith("postgresql+asyncpg://"):
        problems.append("DATABASE_URL must be a postgresql+asyncpg:// URL")
    expected = (environ.get("KLAROS_PILOT_DB_NAME") or "").strip()
    if env in REACHABLE and not expected:
        problems.append("KLAROS_PILOT_DB_NAME is not set (the pilot database's name; it must match the database actually connected to)")
    mig = environ.get("DATABASE_MIGRATION_URL")
    if mig:
        if not mig.startswith("postgresql+asyncpg://"):
            problems.append("DATABASE_MIGRATION_URL must be a postgresql+asyncpg:// URL")
        if environ.get("DATABASE_URL") and url_identity(mig) != url_identity(environ["DATABASE_URL"]):
            problems.append("DATABASE_MIGRATION_URL and DATABASE_URL must point at the same database name and host")
    for var in ("DATABASE_URL", "DATABASE_MIGRATION_URL"):
        url = environ.get(var)
        if url and expected:
            name, host = url_identity(url)
            if name != expected:
                problems.append(f"{var} points at a database that is not KLAROS_PILOT_DB_NAME")
            if name_forbidden(url, environ):
                problems.append(f"{var} points at a forbidden database or host (KLAROS_FORBIDDEN_DB_NAMES / KLAROS_FORBIDDEN_DB_HOSTS)")
        elif url and name_forbidden(url, environ):
            problems.append(f"{var} points at a forbidden database or host")
    if env in REACHABLE:
        if (environ.get("JWT_SECRET") or INSECURE_JWT) == INSECURE_JWT:
            problems.append(f"JWT_SECRET is unset or the insecure default (ENV={env})")
        if not environ.get("INTEGRATION_CREDENTIAL_ENCRYPTION_KEY"):
            problems.append(f"INTEGRATION_CREDENTIAL_ENCRYPTION_KEY is not set (ENV={env})")
    if tenants:
        if (environ.get("WORKFORCE_ADAPTER") or "").lower() != "halla":
            problems.append("WORKFORCE_ADAPTER must be 'halla' when HALLA_PILOT_TENANTS is set")
        for name in ("HALLA_API_BASE_URL", "HALLA_API_KEY_HEADER", "KLAROS_PUBLIC_API_URL"):
            if not environ.get(name):
                problems.append(f"{name} is not set")
        for t in tenants:
            for var in (t.api_key_env, t.secret_env) + ((t.owner_password_env,) if t.owner_password_env else ()):
                if not environ.get(var):
                    problems.append(f"{var} is not set (needed for tenant '{t.slug}')")
    return problems


async def refuse_foreign_database(url: str, expected_name: str = "") -> dict[str, object]:
    """Never run migrations against a database that is not Klaros's own. An existing Prisma database (it has `_prisma_migrations`) or any
    non-empty database that Alembic has never managed is refused — BEFORE any statement that changes anything."""
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            tables = set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
            live_name, vector, installed, can_create, superuser = None, None, None, None, None
            if conn.dialect.name == "postgresql":
                from sqlalchemy import text

                live_name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
                vector = bool((await conn.execute(text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"))).scalar_one())
                installed = bool((await conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'"))).scalar_one())
                superuser = bool((await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar_one())
                trusted = bool((await conn.execute(text("SELECT coalesce(bool_or(trusted), false) FROM pg_available_extension_versions WHERE name = 'vector'"))).scalar_one())
                db_create = bool((await conn.execute(text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')"))).scalar_one())
                # CREATE EXTENSION needs superuser, or a TRUSTED extension plus CREATE on the database (PostgreSQL 13+).
                can_create = installed or superuser or (trusted and db_create)
    finally:
        await engine.dispose()
    if expected_name and live_name is not None and live_name != expected_name:
        raise ConfigError("Refusing to continue: the database actually connected to is not KLAROS_PILOT_DB_NAME.")
    if vector is False:
        raise ConfigError("Refusing to continue: this PostgreSQL does not offer the 'vector' extension (pgvector). Use a provider/plan that has it, or enable it first.")
    if can_create is False:
        raise ConfigError("Refusing to continue: the 'vector' extension is not installed and this database role cannot create it. Enable it once in the provider's dashboard/SQL editor (CREATE EXTENSION vector;) or use an owner role in DATABASE_MIGRATION_URL.")
    if "_prisma_migrations" in tables:
        raise ConfigError("Refusing to continue: this database contains a Prisma migration table, so it is not a Klaros database.")
    if tables and "alembic_version" not in tables:
        raise ConfigError("Refusing to continue: this database already has tables but no Alembic history, so it is not a Klaros database.")
    return {"database": live_name, "tables": len(tables), "vector_available": vector, "vector_installed": installed, "role_can_create_vector": can_create,
            "role_is_superuser": superuser, "alembic_managed": "alembic_version" in tables}


def run_migrations(environ: Mapping[str, str]) -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env={**os.environ, **environ})


async def ensure_tenant(t: PilotTenant, environ: Mapping[str, str]) -> dict[str, Any]:
    """Create the organization (and, when configured, its owner) if absent. Existing rows are NEVER changed: an existing owner's password
    is not reset, an existing organization is not renamed."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.session import async_session_maker, set_tenant_context
    from app.models.organization import Organization
    from app.models.rbac import Role
    from app.models.user import User

    result = {"slug": t.slug, "organization": "exists", "owner": "not configured"}
    async with async_session_maker() as db:
        org = (await db.execute(select(Organization).where(Organization.id == t.klaros_tenant_id))).scalar_one_or_none()
        if org is None:
            clash = (await db.execute(select(Organization).where(Organization.slug == t.slug))).scalar_one_or_none()
            if clash is not None:
                raise ConfigError(f"slug '{t.slug}' already belongs to a different organization")
            db.add(Organization(
                id=t.klaros_tenant_id, name=t.name, slug=t.slug, plan="growth", billing_status="trialing",
                trial_ends_at=datetime.now(UTC) + timedelta(days=t.trial_days),
            ))
            await db.commit()
            result["organization"] = "created"
        if t.owner_email:
            await set_tenant_context(db, t.klaros_tenant_id)
            existing = (await db.execute(select(User).where(User.tenant_id == t.klaros_tenant_id, User.email == t.owner_email))).scalar_one_or_none()
            if existing is None:
                db.add(User(tenant_id=t.klaros_tenant_id, email=t.owner_email, hashed_password=hash_password(environ[t.owner_password_env]),
                            full_name="Pilot owner", role=Role.OWNER))
                await db.commit()
                result["owner"] = "created"
            else:
                result["owner"] = "exists (password unchanged)"
    return result


async def set_safety_profile(t: PilotTenant) -> None:
    """Record the tenant's safety profile (non-secret) on its Halla connection, merging with whatever metadata is already there."""
    from sqlalchemy import select

    from app.db.session import async_session_maker, set_tenant_context
    from app.models.integration import IntegrationConnection

    async with async_session_maker() as db:
        await set_tenant_context(db, t.klaros_tenant_id)
        conn = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == t.klaros_tenant_id, IntegrationConnection.provider == "halla"))).scalar_one()
        conn.connection_metadata = {**(conn.connection_metadata or {}), "safety_profile": t.safety_profile}
        await db.commit()


async def connect_tenant(t: PilotTenant, environ: Mapping[str, str], *, verify: bool) -> dict[str, Any]:
    """(Re-)save this tenant's Halla connection from the environment. Never raises for an unreachable Halla: the credential is stored
    either way, and the outcome is reported honestly."""
    from scripts.connect_halla_tenant import connect

    try:
        r = await connect(t.klaros_tenant_id, t.halla_tenant_id, environ, verify=verify, key_var=t.api_key_env, secret_var=t.secret_env)
        await set_safety_profile(t)
        return {"slug": t.slug, "connection": r["connection_status"], "detail": r["status"]}
    except SystemExit as exc:  # a missing secret: report, do not crash the deploy
        return {"slug": t.slug, "connection": "NOT_SAVED", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a Halla outage must not take Klaros down; the type only, never the message
        return {"slug": t.slug, "connection": "NOT_SAVED", "detail": type(exc).__name__}


def say(msg: str) -> None:
    print(f"[start] {msg}", flush=True)


async def check_only(environ: Mapping[str, str]) -> int:
    """Read-only verification the owner can run where the secrets are set (`python -m scripts.start --check`): configuration, which database the URLs
    really point at, pgvector, the role's ability to create it. It changes NOTHING (no migration, no tenant, no Halla call) and prints no secret."""
    tenants = parse_tenants(environ)
    problems = preflight(environ, tenants)
    for p in problems:
        say(f"CHECK FAILED: {p}")
    if problems:
        return 2
    url = environ.get("DATABASE_MIGRATION_URL") or environ["DATABASE_URL"]
    name, host = url_identity(url)
    info = await refuse_foreign_database(url, (environ.get("KLAROS_PILOT_DB_NAME") or "").strip())
    say(f"target database: '{info['database'] or name}' on host '{host}'")
    say(f"matches KLAROS_PILOT_DB_NAME: yes; forbidden names/hosts: none matched; url scheme: postgresql+asyncpg")
    say(f"database state: {'already Klaros-managed (Alembic history present)' if info['alembic_managed'] else 'empty (no tables)'}, tables: {info['tables']}")
    say(f"pgvector: available={info['vector_available']}, installed={info['vector_installed']}, this role can create it={info['role_can_create_vector']}, superuser={info['role_is_superuser']}")
    say("CHECK PASSED — nothing was changed")
    return 0


async def setup(environ: Mapping[str, str], *, skip_migrate: bool, verify: bool) -> int:
    tenants = parse_tenants(environ)
    problems = preflight(environ, tenants)
    if problems:
        for p in problems:
            say(f"PREFLIGHT FAILED: {p}")
        return 2
    url = environ.get("DATABASE_MIGRATION_URL") or environ["DATABASE_URL"]
    info = await refuse_foreign_database(url, (environ.get("KLAROS_PILOT_DB_NAME") or "").strip())
    say(f"database check passed: '{info['database']}' ({'already Klaros-managed' if info['alembic_managed'] else 'empty'}; pgvector available: {info['vector_available']})")
    if not skip_migrate:
        run_migrations(environ)
        say("migrations at head")
    for t in tenants:
        r = await ensure_tenant(t, environ)
        say(f"tenant {t.slug} ({mask(t.klaros_tenant_id, 8)}): organization {r['organization']}, owner {r['owner']}")
    for t in tenants:
        r = await connect_tenant(t, environ, verify=verify)
        say(f"tenant {t.slug}: halla connection {r['connection']} ({r['detail']}); halla tenant {mask(t.halla_tenant_id)}")
    return 0


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-serve", action="store_true")
    parser.add_argument("--skip-migrate", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--check", action="store_true", help="read-only: verify configuration and the target database identity, then stop")
    args = parser.parse_args(argv)
    env = dict(environ if environ is not None else os.environ)
    try:
        code = asyncio.run(check_only(env)) if args.check else asyncio.run(setup(env, skip_migrate=args.skip_migrate, verify=not args.skip_verify))
    except ConfigError as exc:
        say(f"STOPPED: {exc}")
        return 2
    if code != 0 or args.no_serve or args.check:  # --check is read-only and never starts the server
        return code
    port = env.get("PORT") or "8000"
    say(f"starting the API on port {port}")
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port])
    return 0  # pragma: no cover - exec does not return


if __name__ == "__main__":
    sys.exit(main())
