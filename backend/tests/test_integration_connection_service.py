"""Phase 12D: tenant-scoped integration connection lifecycle + tenant
isolation. Uses a fake, deterministic test-only verifier (not a real
provider) — this suite tests OUR OWN lifecycle/isolation logic, which must
be correct regardless of which real provider is eventually plugged in."""

import asyncio
import uuid

import pytest

from app.db.session import async_session_maker
from app.integrations.credential_store import decrypt_credential
from app.models.integration import ConnectionStatus, IntegrationConnection
from app.services.integration_connection_service import (
    ConnectionNotFoundError,
    IntegrationConnectionService,
)

pytestmark = pytest.mark.asyncio


async def _always_ok(credential: dict) -> tuple[bool, str]:
    return True, "verified (test)"


async def _always_fails(credential: dict) -> tuple[bool, str]:
    return False, "credential rejected (test)"


@pytest.fixture
def service() -> IntegrationConnectionService:
    svc = IntegrationConnectionService(async_session_maker)
    svc.register_verifier("test_provider_ok", _always_ok)
    svc.register_verifier("test_provider_bad", _always_fails)
    return svc


# --- Production observability: real-time owner alert on auth failure,
# with real deduplication (never one alert per re-check). ---

async def test_verify_failure_notifies_owner_once_via_real_notification_service() -> None:
    from app.models.notification import Notification, NotificationType
    from app.services.notification_service import NotificationService
    from sqlalchemy import select

    tenant_id = uuid.uuid4()
    notifications = NotificationService(async_session_maker)
    svc = IntegrationConnectionService(async_session_maker, notifications)
    svc.register_verifier("test_provider_bad", _always_fails)
    await svc.connect(tenant_id, "test_provider_bad", {"api_key": "whatever"}, created_by=None)

    await svc.verify(tenant_id, "test_provider_bad")
    await svc.verify(tenant_id, "test_provider_bad")  # a second failure the SAME day

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.tenant_id == tenant_id, Notification.type == NotificationType.SYSTEM_ERROR,
                )
            )
        ).scalars().all()
    assert len(rows) == 1  # deduplicated — not one per verify() call
    assert "test_provider_bad" in rows[0].title


async def test_verify_success_never_notifies() -> None:
    from app.models.notification import Notification
    from app.services.notification_service import NotificationService
    from sqlalchemy import select

    tenant_id = uuid.uuid4()
    notifications = NotificationService(async_session_maker)
    svc = IntegrationConnectionService(async_session_maker, notifications)
    svc.register_verifier("test_provider_ok", _always_ok)
    await svc.connect(tenant_id, "test_provider_ok", {"api_key": "whatever"}, created_by=None)

    await svc.verify(tenant_id, "test_provider_ok")

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
    assert rows == []


async def test_verify_failure_without_a_notification_service_never_raises() -> None:
    """The optional dependency stays optional — every existing call site
    that never passes one (all of them, before this phase) keeps working
    exactly as before."""
    tenant_id = uuid.uuid4()
    svc = IntegrationConnectionService(async_session_maker)  # no notification_service, same as every pre-existing test
    svc.register_verifier("test_provider_bad", _always_fails)
    await svc.connect(tenant_id, "test_provider_bad", {"api_key": "whatever"}, created_by=None)

    connection = await svc.verify(tenant_id, "test_provider_bad")
    assert connection.status == ConnectionStatus.ERROR


async def test_connect_with_working_credential_reaches_connected(service) -> None:
    tenant_id = uuid.uuid4()
    connection = await service.connect(
        tenant_id, "test_provider_ok", {"api_key": "fake-key-1"}, created_by=None
    )
    assert connection.status == ConnectionStatus.CONNECTED
    assert connection.last_verified_at is not None
    assert connection.last_error is None


async def test_connect_with_bad_credential_reaches_error_not_connected(service) -> None:
    tenant_id = uuid.uuid4()
    connection = await service.connect(
        tenant_id, "test_provider_bad", {"api_key": "fake-key-2"}, created_by=None
    )
    assert connection.status == ConnectionStatus.ERROR
    assert connection.last_error == "credential rejected (test)"


async def test_connect_to_a_provider_with_no_registered_verifier_is_honest_error(service) -> None:
    """Must never claim CONNECTED for a provider with no real verification
    path — this is the "no fabricated success" rule enforced structurally."""
    tenant_id = uuid.uuid4()
    connection = await service.connect(
        tenant_id, "quickbooks", {"client_id": "fake"}, created_by=None
    )
    assert connection.status == ConnectionStatus.ERROR
    assert "no real verifier" in connection.last_error.lower()


async def test_credential_is_encrypted_at_rest_in_the_database(service) -> None:
    tenant_id = uuid.uuid4()
    secret_value = "super-secret-fake-api-key-xyz"
    await service.connect(tenant_id, "test_provider_ok", {"api_key": secret_value}, created_by=None)

    async with async_session_maker() as session:
        from sqlalchemy import select

        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.tenant_id == tenant_id,
                    IntegrationConnection.provider == "test_provider_ok",
                )
            )
        ).scalar_one()
        assert secret_value not in row.encrypted_credential
        assert decrypt_credential(row.encrypted_credential) == {"api_key": secret_value}


async def test_disconnect_clears_credential_and_sets_status(service) -> None:
    tenant_id = uuid.uuid4()
    await service.connect(tenant_id, "test_provider_ok", {"api_key": "fake"}, created_by=None)
    disconnected = await service.disconnect(tenant_id, "test_provider_ok")
    assert disconnected.status == ConnectionStatus.DISCONNECTED
    assert disconnected.encrypted_credential is None


async def test_reverify_updates_status_when_credential_changes(service) -> None:
    tenant_id = uuid.uuid4()
    await service.connect(tenant_id, "test_provider_ok", {"api_key": "fake"}, created_by=None)
    # Re-connect with a provider whose verifier now fails — simulates a
    # revoked/expired credential being re-checked.
    service.register_verifier("test_provider_ok", _always_fails)
    reverified = await service.verify(tenant_id, "test_provider_ok")
    assert reverified.status == ConnectionStatus.ERROR


async def test_hung_verifier_times_out_instead_of_hanging_forever(service, monkeypatch) -> None:
    """A verifier is a real external API call — one that never returns
    must fail fast (ERROR), not hang the request indefinitely. Same class
    of bug found and fixed in Phase 12B's /ready endpoint."""
    monkeypatch.setattr(service, "VERIFY_TIMEOUT_SECONDS", 0.2)

    async def _hangs_forever(credential: dict) -> tuple[bool, str]:
        await asyncio.sleep(999)
        return True, "unreachable"

    service.register_verifier("test_provider_hangs", _hangs_forever)
    tenant_id = uuid.uuid4()

    connection = await asyncio.wait_for(
        service.connect(tenant_id, "test_provider_hangs", {"api_key": "fake"}, created_by=None),
        timeout=5,
    )
    assert connection.status == ConnectionStatus.ERROR
    assert "timed out" in connection.last_error.lower()


async def test_verify_on_nonexistent_connection_raises(service) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(ConnectionNotFoundError):
        await service.verify(tenant_id, "test_provider_ok")


async def test_disconnect_on_nonexistent_connection_raises(service) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(ConnectionNotFoundError):
        await service.disconnect(tenant_id, "test_provider_ok")


# --- Tenant isolation ---


async def test_tenant_a_connection_is_invisible_to_tenant_b(service) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await service.connect(tenant_a, "test_provider_ok", {"api_key": "tenant-a-key"}, created_by=None)

    b_connection = await service.get_connection(tenant_b, "test_provider_ok")
    assert b_connection is None

    b_list = await service.list_connections(tenant_b)
    assert b_list == []


async def test_tenant_b_cannot_verify_tenant_as_connection(service) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await service.connect(tenant_a, "test_provider_ok", {"api_key": "tenant-a-key"}, created_by=None)

    with pytest.raises(ConnectionNotFoundError):
        await service.verify(tenant_b, "test_provider_ok")


async def test_tenant_b_cannot_disconnect_tenant_as_connection(service) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await service.connect(tenant_a, "test_provider_ok", {"api_key": "tenant-a-key"}, created_by=None)

    with pytest.raises(ConnectionNotFoundError):
        await service.disconnect(tenant_b, "test_provider_ok")

    # Tenant A's connection must remain untouched by tenant B's failed attempt.
    a_connection = await service.get_connection(tenant_a, "test_provider_ok")
    assert a_connection.status == ConnectionStatus.CONNECTED
    assert a_connection.encrypted_credential is not None


async def test_two_tenants_can_independently_connect_the_same_provider(service) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    conn_a = await service.connect(tenant_a, "test_provider_ok", {"api_key": "key-a"}, created_by=None)
    conn_b = await service.connect(tenant_b, "test_provider_ok", {"api_key": "key-b"}, created_by=None)

    assert conn_a.id != conn_b.id
    assert conn_a.tenant_id == tenant_a
    assert conn_b.tenant_id == tenant_b

    async with async_session_maker() as session:
        row_a = await session.get(IntegrationConnection, conn_a.id)
        row_b = await session.get(IntegrationConnection, conn_b.id)
        assert decrypt_credential(row_a.encrypted_credential) == {"api_key": "key-a"}
        assert decrypt_credential(row_b.encrypted_credential) == {"api_key": "key-b"}
