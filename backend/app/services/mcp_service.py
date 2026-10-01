"""Phase 9: service layer for the two MCP-server-only admin concerns —
exposure-policy management and credential issuance/revocation/
authentication. No execution logic lives here; `ToolRegistry.execute()`
remains the sole execution boundary (see app/mcp/protocol.py, which is the
only caller of `McpCredentialService.authenticate()` +
`McpExposureService.list_enabled_tool_names()` outside this module's own
tests).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import discovery_session_maker, set_tenant_context
from app.models.mcp_server import McpClientCredential, McpCredentialStatus, McpToolExposure
from app.models.rbac import Role

_TOKEN_PREFIX = "mcpkl_"


class McpCredentialError(Exception):
    pass


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedCredential:
    id: uuid.UUID
    raw_token: str  # shown to the caller exactly once — never persisted
    name: str
    role: Role


class McpExposureService:
    """The exposure allowlist — see app/models/mcp_server.py's module
    docstring. Every method is tenant-scoped by an explicit `tenant_id`
    argument, never inferred from anything client-supplied."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def list_exposures(self, tenant_id: uuid.UUID) -> list[McpToolExposure]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(
                select(McpToolExposure)
                .where(McpToolExposure.tenant_id == tenant_id)
                .order_by(McpToolExposure.tool_name)
            )
            return list(result.scalars().all())

    async def list_enabled_tool_names(self, tenant_id: uuid.UUID) -> set[str]:
        """The ONLY function the MCP protocol layer may consult to decide
        whether a tool call is allowed through — deliberately re-reads the
        DB on every call (no in-process cache) so a revocation takes effect
        immediately, matching the kill-switch's own re-read-every-call
        discipline in ToolRegistry.execute()."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(
                select(McpToolExposure.tool_name).where(
                    McpToolExposure.tenant_id == tenant_id, McpToolExposure.enabled.is_(True)
                )
            )
            return set(result.scalars().all())

    async def set_exposure(
        self, tenant_id: uuid.UUID, tool_name: str, enabled: bool, *, created_by: uuid.UUID | None
    ) -> McpToolExposure:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(
                select(McpToolExposure).where(
                    McpToolExposure.tenant_id == tenant_id, McpToolExposure.tool_name == tool_name
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = McpToolExposure(
                    tenant_id=tenant_id, tool_name=tool_name, enabled=enabled, created_by=created_by
                )
                session.add(row)
            else:
                row.enabled = enabled
            await session.commit()
            await session.refresh(row)
            return row


class McpCredentialService:
    """Issuance/revocation/authentication for `McpClientCredential`. Never
    persists a raw token — only its SHA-256 hash — matching
    `credential_store.py`'s "never store the secret itself" discipline, but
    one-way (nothing ever needs the raw token back after issuance)."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def issue(
        self, tenant_id: uuid.UUID, name: str, role: Role, *, created_by: uuid.UUID | None
    ) -> IssuedCredential:
        raw_token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = McpClientCredential(
                tenant_id=tenant_id,
                name=name,
                role=role.value,
                token_hash=_hash_token(raw_token),
                token_prefix=raw_token[:16],
                status=McpCredentialStatus.ACTIVE,
                created_by=created_by,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return IssuedCredential(id=row.id, raw_token=raw_token, name=row.name, role=role)

    async def list_credentials(self, tenant_id: uuid.UUID) -> list[McpClientCredential]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(
                select(McpClientCredential)
                .where(McpClientCredential.tenant_id == tenant_id)
                .order_by(McpClientCredential.created_at.desc())
            )
            return list(result.scalars().all())

    async def revoke(self, tenant_id: uuid.UUID, credential_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(McpClientCredential, credential_id)
            if row is None or row.tenant_id != tenant_id:
                raise McpCredentialError("Unknown MCP client credential")
            row.status = McpCredentialStatus.REVOKED
            row.revoked_at = datetime.now(UTC)
            await session.commit()

    async def authenticate(self, raw_token: str) -> McpClientCredential | None:
        """Looks up a credential by the SHA-256 hash of the presented raw
        token — never by any client-supplied tenant/id claim, so an
        authenticated MCP client's tenant is established SOLELY from what
        this lookup returns, never from request metadata. Returns None
        (never raises) for any unknown/malformed/revoked token — the
        caller (app/mcp/protocol.py) is responsible for rejecting with a
        generic 401, so a malformed token and a revoked one are
        indistinguishable to the client (no oracle for enumeration).

        Phase 17B-4 (§37d/§39): two real steps, on two DIFFERENT database
        roles, not one query on the ordinary app session — WHEN
        `klaros_discovery` is configured (`DISCOVERY_DATABASE_URL` set).
        `users` has had real, enforcing RLS since `0053`, and
        `mcp_client_credentials` has had it since `0057` — under real RLS,
        an ordinary `klaros_app` session with no tenant context set (which
        is unavoidable here: resolving the tenant IS what this function is
        for) sees zero rows, always, regardless of how correct the
        presented token is. Step 1 below resolves ONLY which tenant (if
        any) owns this token hash, through the narrow, read-only
        `klaros_discovery` role (never `klaros_app`) — proven,
        real-Postgres-tested, to be able to read exactly
        `id`/`tenant_id`/`token_hash`/`status` on this table and nothing
        else, and to have zero DML capability anywhere. Step 2 then opens
        a genuine, ordinary per-tenant `klaros_app` session, stamped with
        the tenant_id step 1 resolved, and does the real work (full-row
        fetch + the `last_used_at` write) with real RLS protecting it like
        any other tenant-scoped write.

        WHEN `klaros_discovery` is NOT configured (`DISCOVERY_DATABASE_URL`
        unset — every environment that hasn't provisioned it yet,
        including this codebase's own default SQLite/single-role test
        suite, and any Postgres deployment still on the single
        owner/superuser role this whole phase has been migrating away
        from), falls back to the original single-query behavior: a
        direct, unscoped lookup on the ordinary session. This is not a
        weaker security posture than before this round — it is exactly
        the behavior every environment already had prior to this fix, and
        it degrades safely: an environment that HAS cut over to real RLS
        + the restricted `klaros_app` role but has NOT yet provisioned
        `klaros_discovery` will, correctly, see this fallback also find
        nothing (real RLS blocks the unscoped query same as any other),
        which is the fail-closed outcome §37d already documented as
        intentional for that specific gap."""
        if not raw_token or not raw_token.startswith(_TOKEN_PREFIX):
            return None
        token_hash = _hash_token(raw_token)

        if discovery_session_maker is not None:
            tenant_id = await self._resolve_tenant_id_via_discovery(token_hash)
            if tenant_id is None:
                return None
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                result = await session.execute(
                    select(McpClientCredential).where(McpClientCredential.token_hash == token_hash)
                )
                row = result.scalar_one_or_none()
                if row is None or row.status != McpCredentialStatus.ACTIVE:
                    return None
                row.last_used_at = datetime.now(UTC)
                await session.commit()
                return row

        # Fallback: `klaros_discovery` not configured in this environment
        # — the exact pre-Round-15 behavior, preserved unchanged so every
        # environment that hasn't provisioned the discovery role yet
        # (dev/test/SQLite, or a Postgres deployment not yet cut over to
        # the restricted-role model) keeps working exactly as before.
        async with self._session_factory() as session:
            result = await session.execute(
                select(McpClientCredential).where(McpClientCredential.token_hash == token_hash)
            )
            row = result.scalar_one_or_none()
            if row is None or row.status != McpCredentialStatus.ACTIVE:
                return None
            row.last_used_at = datetime.now(UTC)
            await session.commit()
            return row

    @staticmethod
    async def _resolve_tenant_id_via_discovery(token_hash: str) -> uuid.UUID | None:
        """Step 1 of `authenticate()`: which tenant (if any) owns this
        token hash — via the narrow, read-only `klaros_discovery` role,
        never the ordinary `klaros_app` session (see `authenticate()`'s
        own docstring for why). Only called once the caller has already
        confirmed `discovery_session_maker is not None`. Fails closed
        (returns None) when the token doesn't resolve to an ACTIVE
        credential — indistinguishable from an unknown token, matching
        `authenticate()`'s own no-oracle contract."""
        async with discovery_session_maker() as session:
            result = await session.execute(
                select(McpClientCredential.tenant_id, McpClientCredential.status).where(
                    McpClientCredential.token_hash == token_hash
                )
            )
            row = result.first()
            if row is None or row.status != McpCredentialStatus.ACTIVE:
                return None
            return row.tenant_id
