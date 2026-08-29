"""Phase 12F: tenant-scoped Stripe credentials via IntegrationConnection —
a tenant's own connected Stripe account is used over the platform-level
key, and one tenant's key is never usable for another tenant. Mocks
StripeClient.verify_connection (not the HTTP layer) for the connect-flow
tests — test_stripe_client.py already covers the real HTTP/retry/
classification behavior underneath verify_connection separately."""

import uuid

import pytest

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.integration import ConnectionStatus
from app.services.integration_connection_service import IntegrationConnectionService
from app.tools.builtin.stripe_tools import _resolve_stripe_secret_key

pytestmark = pytest.mark.asyncio


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def test_connect_with_valid_stripe_credential_reaches_connected(connection_service, monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    async def _fake_verify_connection(self) -> bool:
        return True

    monkeypatch.setattr(mod.StripeClient, "verify_connection", _fake_verify_connection)

    tenant_id = uuid.uuid4()
    connection = await connection_service.connect(
        tenant_id, "stripe", {"secret_key": "sk_test_fake_valid"}, created_by=None
    )
    assert connection.status == ConnectionStatus.CONNECTED
    assert connection.last_verified_at is not None
    assert connection.last_error is None


async def test_connect_with_invalid_stripe_credential_reaches_error(connection_service, monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    async def _fake_verify_connection(self) -> bool:
        return False

    monkeypatch.setattr(mod.StripeClient, "verify_connection", _fake_verify_connection)

    tenant_id = uuid.uuid4()
    connection = await connection_service.connect(
        tenant_id, "stripe", {"secret_key": "sk_test_fake_invalid"}, created_by=None
    )
    assert connection.status == ConnectionStatus.ERROR
    assert "rejected" in connection.last_error.lower()


async def test_connect_with_missing_secret_key_field_is_honest_error(connection_service) -> None:
    tenant_id = uuid.uuid4()
    connection = await connection_service.connect(
        tenant_id, "stripe", {"wrong_field": "oops"}, created_by=None
    )
    assert connection.status == ConnectionStatus.ERROR
    assert "secret_key" in connection.last_error


async def test_checkout_tool_prefers_tenant_connection_over_platform_key(connection_service, monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    async def _fake_verify_connection(self) -> bool:
        return True

    monkeypatch.setattr(mod.StripeClient, "verify_connection", _fake_verify_connection)

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_platform_key")

    tenant_id = uuid.uuid4()
    await connection_service.connect(tenant_id, "stripe", {"secret_key": "sk_test_tenant_key"}, created_by=None)

    resolved = await _resolve_stripe_secret_key(connection_service, tenant_id)
    assert resolved == "sk_test_tenant_key"


async def test_checkout_tool_falls_back_to_platform_key_with_no_tenant_connection(
    connection_service, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_platform_fallback")

    tenant_id = uuid.uuid4()
    resolved = await _resolve_stripe_secret_key(connection_service, tenant_id)
    assert resolved == "sk_test_platform_fallback"


async def test_tenant_bs_key_is_never_used_for_tenant_a(connection_service, monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    async def _fake_verify_connection(self) -> bool:
        return True

    monkeypatch.setattr(mod.StripeClient, "verify_connection", _fake_verify_connection)

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_platform_key")

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await connection_service.connect(tenant_b, "stripe", {"secret_key": "sk_test_tenant_b_key"}, created_by=None)

    # Tenant A has no connection of its own — must resolve to the platform
    # key, never tenant B's, even though tenant B's row exists in the DB.
    resolved_for_a = await _resolve_stripe_secret_key(connection_service, tenant_a)
    assert resolved_for_a == "sk_test_platform_key"
    assert resolved_for_a != "sk_test_tenant_b_key"


async def test_disconnected_tenant_connection_falls_back_to_platform_key(connection_service, monkeypatch) -> None:
    import app.integrations.stripe_client as mod

    async def _fake_verify_connection(self) -> bool:
        return True

    monkeypatch.setattr(mod.StripeClient, "verify_connection", _fake_verify_connection)

    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_platform_key")

    tenant_id = uuid.uuid4()
    await connection_service.connect(tenant_id, "stripe", {"secret_key": "sk_test_tenant_key"}, created_by=None)
    await connection_service.disconnect(tenant_id, "stripe")

    resolved = await _resolve_stripe_secret_key(connection_service, tenant_id)
    assert resolved == "sk_test_platform_key"
