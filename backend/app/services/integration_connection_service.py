"""Phase 12D: tenant-scoped integration connection lifecycle.

NOT_CONNECTED -> CONNECTING -> CONNECTED -> ERROR -> DISCONNECTED

This is the ONE place that lifecycle logic lives — providers register a
`verify()` callback (real API call, decrypted-credential in, (ok, detail)
out) rather than duplicating connect/verify/disconnect plumbing in every
router or adapter. Tenant isolation is enforced here at the query layer
(every method takes tenant_id and filters by it — there is no method that
can return or mutate another tenant's connection) in addition to the API
layer's auth dependency and the DB's tenant_id column.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.credential_store import decrypt_credential, encrypt_credential
from app.models.integration import ConnectionStatus, IntegrationConnection

logger = structlog.get_logger(__name__)

VerifierFn = Callable[[dict], Awaitable[tuple[bool, str]]]


class ConnectionNotFoundError(Exception):
    pass


class IntegrationConnectionService:
    VERIFY_TIMEOUT_SECONDS = 10.0

    def __init__(self, session_factory: async_sessionmaker, notification_service=None) -> None:
        self._session_factory = session_factory
        self._verifiers: dict[str, VerifierFn] = {}
        self._verifier_timeouts: dict[str, float] = {}
        # Optional (production observability phase) — reuses the existing
        # NotificationService, never a second notification mechanism.
        # None in every existing test/call site that doesn't pass one, so
        # this stays fully backward compatible; only the one real
        # production wiring (app/api/tool_deps_integrations.py) provides
        # a real instance.
        self._notifications = notification_service

    def register_verifier(self, provider: str, verifier: VerifierFn, *, timeout: float | None = None) -> None:
        """A real, provider-specific async function: takes the decrypted
        credential dict, makes a real read-only API call, returns
        (ok, detail). Never fabricate a True here — an unimplemented
        provider should not register a verifier at all (verify() then
        reports ERROR, "no verifier registered", honestly)."""
        self._verifiers[provider] = verifier
        if timeout is not None:
            self._verifier_timeouts[provider] = timeout  # a provider whose real API is legitimately slower than the default

    async def get_connection(self, tenant_id: uuid.UUID, provider: str) -> IntegrationConnection | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.tenant_id == tenant_id,
                        IntegrationConnection.provider == provider,
                    )
                )
            ).scalar_one_or_none()

    async def list_connections(self, tenant_id: uuid.UUID) -> list[IntegrationConnection]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id)
                )
            ).scalars().all()
            return list(rows)

    async def connect(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        credential_data: dict,
        *,
        created_by: uuid.UUID | None,
        external_account_id: str | None = None,
        scopes: str | None = None,
    ) -> IntegrationConnection:
        """Stores the credential (encrypted) and immediately attempts a
        real verification — a connection is never left claiming CONNECTED
        without a real call having actually succeeded. If no verifier is
        registered for this provider, status is ERROR with an honest
        "not implemented" detail, never CONNECTED."""
        encrypted = encrypt_credential(credential_data)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.tenant_id == tenant_id,
                        IntegrationConnection.provider == provider,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.encrypted_credential = encrypted
                existing.external_account_id = external_account_id
                existing.scopes = scopes
                existing.status = ConnectionStatus.CONNECTING
                existing.created_by = created_by
                connection = existing
            else:
                connection = IntegrationConnection(
                    tenant_id=tenant_id,
                    provider=provider,
                    status=ConnectionStatus.CONNECTING,
                    encrypted_credential=encrypted,
                    external_account_id=external_account_id,
                    scopes=scopes,
                    created_by=created_by,
                )
                session.add(connection)

            await session.commit()
            await session.refresh(connection)
            connection_id = connection.id

        logger.info("integration_connection_created", provider=provider, tenant_id=str(tenant_id), operation="connect")
        return await self.verify(tenant_id, provider, connection_id=connection_id)

    async def verify(
        self, tenant_id: uuid.UUID, provider: str, *, connection_id: uuid.UUID | None = None
    ) -> IntegrationConnection:
        """Re-runs the real verification call against the currently-stored
        credential. Always updates last_verified_at (attempted, regardless
        of outcome) and last_error (cleared on success)."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            connection = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.tenant_id == tenant_id,
                        IntegrationConnection.provider == provider,
                    )
                )
            ).scalar_one_or_none()
            if connection is None:
                raise ConnectionNotFoundError(f"No connection for provider {provider}")
            if connection_id is not None and connection.id != connection_id:
                raise ConnectionNotFoundError("Connection id mismatch")

            verifier = self._verifiers.get(provider)
            credential_data = decrypt_credential(connection.encrypted_credential) if connection.encrypted_credential else {}

            started = time.monotonic()
            if verifier is None:
                ok, detail = False, f"No real verifier implemented for provider '{provider}'"
            else:
                try:
                    # Bounded, same reasoning as /ready's dependency checks
                    # (Phase 12B): a verifier is a real, possibly-slow
                    # external API call, and an already-open connection to
                    # a stalled provider has no timeout by default —
                    # without this, one hung provider call would hang this
                    # request indefinitely rather than reporting ERROR.
                    limit = self._verifier_timeouts.get(provider, self.VERIFY_TIMEOUT_SECONDS)
                    ok, detail = await asyncio.wait_for(verifier(credential_data), timeout=limit)
                except TimeoutError:
                    ok, detail = False, f"Verification timed out after {limit}s"
                except Exception as exc:  # noqa: BLE001 — a verifier failure is a real ERROR state, not a crash
                    ok, detail = False, f"Verification raised: {exc}"
            latency_ms = int((time.monotonic() - started) * 1000)

            # Observability (Phase 12D Step 13): provider/operation/tenant/
            # latency/success — never the credential_data dict itself, and
            # `detail` is a short human-readable status string from the
            # verifier, never a raw exception with request/response bodies.
            logger.info(
                "integration_connection_verified",
                provider=provider,
                tenant_id=str(tenant_id),
                operation="verify",
                success=ok,
                latency_ms=latency_ms,
                has_verifier=verifier is not None,
            )

            connection.status = ConnectionStatus.CONNECTED if ok else ConnectionStatus.ERROR
            connection.last_verified_at = datetime.now(timezone.utc)
            connection.last_error = None if ok else detail

            await session.commit()
            await session.refresh(connection)

        # Real-time owner alert on a genuine auth failure — deliberately
        # NOT sent on every failed verify() call (that would fire every
        # time the Owner Cockpit re-checks status, an alert storm): the
        # dedupe_key is scoped to one calendar day per tenant+provider,
        # so a failing connection notifies once, then stays quiet until
        # either it's fixed or the day rolls over — reuses
        # NotificationService's own existing dedupe mechanism, never a
        # second one.
        if not ok and self._notifications is not None:
            today = datetime.now(timezone.utc).date().isoformat()
            try:
                from app.models.notification import NotificationPriority, NotificationType

                await self._notifications.notify(
                    tenant_id, NotificationType.SYSTEM_ERROR,
                    title=f"{provider} connection failed", body=detail,
                    priority=NotificationPriority.HIGH, entity_type="integration_connection",
                    entity_id=connection.id, dedupe_key=f"provider-auth-failed:{tenant_id}:{provider}:{today}",
                )
            except Exception as exc:  # noqa: BLE001 — a notification failure must never fail the verify() call itself
                logger.error("integration_connection_notify_failed", provider=provider, error=str(exc))

        return connection

    async def disconnect(self, tenant_id: uuid.UUID, provider: str) -> IntegrationConnection:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            connection = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.tenant_id == tenant_id,
                        IntegrationConnection.provider == provider,
                    )
                )
            ).scalar_one_or_none()
            if connection is None:
                raise ConnectionNotFoundError(f"No connection for provider {provider}")

            connection.status = ConnectionStatus.DISCONNECTED
            connection.encrypted_credential = None
            connection.last_error = None

            await session.commit()
            await session.refresh(connection)

        logger.info("integration_connection_disconnected", provider=provider, tenant_id=str(tenant_id), operation="disconnect")
        return connection
